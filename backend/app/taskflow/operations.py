"""Durable steps: reserve, call the configured Director outside the lock, commit."""
import json

from backend.app.budgets.runtime import SpendDenied
from uuid import uuid4

from .models import HermesProvenance
from .ports import OperationContext, ProducedArtifact, UncertainOperation
from .redaction import redact
from .service import FlowError, Taskflow, digest

MAX_OPERATION_BYTES = 1024 * 1024


class _SavedDirector:
    """Pure replay of a durable result, safe to use inside the final transaction."""
    def __init__(self, result, failed, provenance=None):
        self.result, self.failed = result, failed
        self.provenance = provenance or {}

    def produce(self, *args):
        if self.failed:
            raise RuntimeError('saved operation failed')
        return self.result


class StepOperations:
    def __init__(self, flow: Taskflow):
        self.flow, self.conn = flow, flow.conn

    def _operation(self, operation_id):
        row = self.conn.execute('SELECT * FROM taskflow_operations WHERE id=?', (operation_id,)).fetchone()
        if row is None:
            raise FlowError('operation_not_found', 'Операция не найдена', 404)
        return dict(row)

    def list_for_run(self, run_id):
        with self.flow.transaction():
            self.flow._run(run_id)
            return [dict(r) for r in self.conn.execute("""SELECT id,run_id,role,expected_version,state,cancel_requested,
                created_at,updated_at FROM taskflow_operations WHERE run_id=? ORDER BY created_at,id""", (run_id,))]

    def step(self, run_id, request):
        try:
            return self._step(run_id,request)
        except SpendDenied as error:
            # Reservation failed before dispatch; its parent transaction rolled back.
            runtime = getattr(self.flow.director,'spending',None)
            if runtime is not None:
                row = self.flow._run(run_id)
                runtime.event(row['task_id'],'omniroute',self.flow.director.config.model,error.code)
            raise FlowError(error.code,'Запрос модели не отправлен: проверьте тарифы и подтверждение бюджета.',402) from None

    def _step(self, run_id, request):
        key = digest(request.idempotency_key)
        fingerprint = digest({'endpoint': 'commands', **request.model_dump()})
        worker = uuid4().hex
        with self.flow.transaction(write=True):
            row = self.flow._run(run_id)
            saved = self.conn.execute('SELECT request_hash,response_json FROM taskflow_commands WHERE run_id=? AND key_hash=?', (run_id,key)).fetchone()
            if saved:
                if saved['request_hash'] != fingerprint:
                    raise FlowError('idempotency_conflict', 'Ключ уже использован с другим запросом')
                return self.flow.parse_snapshot(saved['response_json'])
            old = self.conn.execute('SELECT * FROM taskflow_operations WHERE run_id=? AND key_hash=?', (run_id,key)).fetchone()
            if old:
                if old['request_hash'] != fingerprint:
                    raise FlowError('idempotency_conflict', 'Ключ уже использован с другим запросом')
                if old['state'] == 'unknown':
                    raise FlowError('operation_unknown', 'Исход неизвестен; требуется явное разрешение без повторного запуска')
                if old['state'] != 'result_ready':
                    raise FlowError('operation_pending', 'Операция уже выполняется; повторный запуск запрещён')
                operation_id, execute = old['id'], False
            else:
                if row['version'] != request.expected_version:
                    raise FlowError('stale_version', 'Версия изменилась; перечитайте состояние процесса')
                if self.flow.mode=='hermes' and row['director_binding']!=getattr(self.flow.director,'binding',''):
                    raise FlowError('director_config_changed', 'Маршрут или версия Hermes изменены; создайте новый процесс')
                self.flow._require(row, {'running'})
                if row['steps_done'] >= row['max_steps']:
                    raise FlowError('step_limit', 'Лимит шагов исчерпан')
                if self.conn.execute("SELECT 1 FROM taskflow_operations WHERE run_id=? AND state IN ('running','result_ready','unknown')", (run_id,)).fetchone():
                    raise FlowError('operation_pending', 'Другой шаг уже зарезервирован')
                snapshot = self.flow._snapshot(run_id)
                input_json = snapshot.model_dump_json()
                goal = redact(self.flow._task(row['task_id']))
                if len(input_json.encode()) + len(goal.encode()) > MAX_OPERATION_BYTES:
                    raise FlowError('operation_input_too_large', 'Вход операции превышает допустимый размер', 422)
                operation_id, execute = uuid4().hex, True
                reserve = getattr(self.flow.director,'reserve_budget',None)
                if callable(reserve):
                    reserve(self.conn,operation_id=operation_id,task_id=row['task_id'],run_id=run_id,
                            role=self.flow._next_role(run_id),goal=goal,inputs=snapshot.artifacts)
                self.conn.execute("""INSERT INTO taskflow_operations(id,run_id,key_hash,request_hash,expected_version,
                    role,goal,input_json,reason,state,worker_hash) VALUES(?,?,?,?,?,?,?,?,?,'running',?)""",
                    (operation_id,run_id,key,fingerprint,request.expected_version,self.flow._next_role(run_id),goal,input_json,redact(request.reason),digest(worker)))

        if execute:
            # No transaction or write lock exists during Director execution.
            with self.flow.transaction():
                operation = self._operation(operation_id)
                if operation['state'] != 'running' or operation['worker_hash'] != digest(worker):
                    raise FlowError('operation_unknown', 'Рабочая попытка больше не владеет операцией')
            result, failed = None, True
            if not operation['cancel_requested']:
                inputs = self.flow.parse_snapshot(operation['input_json']).artifacts
                try:
                    method = getattr(self.flow.director, 'produce_with_context', None)
                    if callable(method):
                        def cancelled():
                            current = self._operation(operation_id)
                            return bool(current['cancel_requested'] or current['state'] != 'running')
                        produced = method(operation['role'], operation['goal'], inputs, OperationContext(operation_id,cancelled))
                    else:
                        produced = self.flow.director.produce(operation['role'], operation['goal'], inputs)
                    provenance = {}
                    if self.flow.mode == 'hermes':
                        if not isinstance(produced, ProducedArtifact):
                            raise ValueError('Missing Hermes provenance')
                        provenance = HermesProvenance.model_validate(produced.provenance).model_dump()
                        if provenance['operation_id'] != operation_id:
                            raise ValueError('Operation provenance mismatch')
                        produced = produced.content
                    raw = produced.model_dump() if hasattr(produced, 'model_dump') else produced
                    result = self.flow.validate_content(operation['role'], raw).model_dump()
                    if len(json.dumps(result).encode()) > MAX_OPERATION_BYTES:
                        raise ValueError('large result')
                    failed = False
                except UncertainOperation:
                    with self.flow.transaction(write=True):
                        finish = getattr(self.flow.director,'finish_budget',None)
                        if callable(finish):finish(operation_id)
                        current=self._operation(operation_id)
                        cancelled=bool(current['cancel_requested'])
                        if cancelled:
                            self.conn.execute("UPDATE taskflow_operations SET state='result_ready',result_json='null',failed=1 WHERE id=? AND state='running'", (operation_id,))
                        else:
                            self.conn.execute("UPDATE taskflow_operations SET state='unknown',worker_hash='' WHERE id=? AND state='running'", (operation_id,))
                    if cancelled:
                        return self.finish(operation_id)
                    raise FlowError('operation_unknown', 'Исход вызова Hermes неизвестен; автоматического повтора нет') from None
                except SpendDenied as error:
                    runtime = getattr(self.flow.director,'spending',None)
                    if runtime is not None:
                        runtime.event(self.flow._run(run_id)['task_id'],
                                      'omniroute',self.flow.director.config.model,error.code,operation_id)
                    result = None
                except Exception:
                    # Arbitrary exception text is never stored. A process crash is not caught.
                    result = None
            with self.flow.transaction(write=True):
                operation = self._operation(operation_id)
                if operation['state'] != 'running' or operation['worker_hash'] != digest(worker):
                    raise FlowError('operation_unknown', 'Рабочая попытка больше не владеет операцией')
                finish = getattr(self.flow.director,'finish_budget',None)
                if callable(finish):finish(operation_id)
                self.conn.execute("""UPDATE taskflow_operations SET state='result_ready',result_json=?,failed=?,provenance_json=?,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""", (json.dumps(result),int(failed),json.dumps(provenance if not failed else {}),operation_id))
        return self.finish(operation_id)

    def finish(self, operation_id):
        """Commit a durable result; never invokes a Director or external effect."""
        with self.flow.transaction(write=True):
            operation = self._operation(operation_id)
            saved = self.conn.execute('SELECT response_json FROM taskflow_commands WHERE run_id=? AND key_hash=?', (operation['run_id'],operation['key_hash'])).fetchone()
            if saved:
                return self.flow.parse_snapshot(saved['response_json'])
            if operation['state'] != 'result_ready':
                raise FlowError('operation_unknown', 'Нет сохранённого результата для фиксации')
            row = self.flow._run(operation['run_id'])
            if operation['cancel_requested'] or row['version'] != operation['expected_version'] or row['status'] != 'running':
                self.conn.execute("UPDATE taskflow_operations SET state='discarded',result_json=NULL,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (operation_id,))
                snapshot = self.flow._snapshot(row['id'])
                self.conn.execute('INSERT INTO taskflow_commands(run_id,key_hash,request_hash,response_json) VALUES(?,?,?,?)', (row['id'],operation['key_hash'],operation['request_hash'],snapshot.model_dump_json()))
                return snapshot
            # Only a pure, saved result is evaluated under the write transaction.
            cached = Taskflow(self.conn, _SavedDirector(json.loads(operation['result_json']), bool(operation['failed']),json.loads(operation['provenance_json'])),mode=self.flow.mode)
            snapshot = cached._commit_action(row, 'step', operation['reason'], operation['key_hash'], operation['request_hash'])
            state = 'failed' if snapshot.status == 'failed' else 'succeeded'
            self.conn.execute("UPDATE taskflow_operations SET state=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (state,operation_id))
            return snapshot

    def mark_unknown(self, operation_id):
        """Operator-only recovery after confirming the previous worker stopped."""
        with self.flow.transaction(write=True):
            operation = self._operation(operation_id)
            if operation['state'] != 'running':
                raise FlowError('invalid_operation_state', 'Восстановление доступно только для выполнявшейся операции')
            self.conn.execute("UPDATE taskflow_operations SET state='unknown',worker_hash='',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (operation_id,))

    def resolve_unknown(self, operation_id, *, result=None):
        """Explicit evidence import; no automatic retry. None records a failed attempt."""
        with self.flow.transaction(write=True):
            operation = self._operation(operation_id)
            if operation['state'] != 'unknown':
                raise FlowError('invalid_operation_state', 'Исход уже разрешён или ещё не признан неизвестным')
            cleaned = None
            if result is not None:
                if self.flow.mode == 'hermes':
                    raise FlowError('provenance_required', 'Импорт реального результата требует отдельной проверенной provenance', 422)
                raw = result.model_dump() if hasattr(result, 'model_dump') else result
                try:
                    cleaned = self.flow.validate_content(operation['role'], raw).model_dump()
                    if len(json.dumps(cleaned).encode()) > MAX_OPERATION_BYTES:
                        raise ValueError
                except Exception:
                    raise FlowError('invalid_operation_result', 'Результат не соответствует сохранённой роли', 422) from None
            self.conn.execute("UPDATE taskflow_operations SET state='result_ready',result_json=?,failed=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (json.dumps(cleaned),int(result is None),operation_id))
        return self.finish(operation_id)
