"""Application-owned transactions must not split operation and budget commits."""
import sqlite3
from contextlib import closing

import pytest

from backend.app.budgets.ledger import BudgetLedger
from backend.app.budgets.models import Policy, Route, Tariff, Request


def test_budget_reservation_rolls_back_with_parent_operation(tmp_path):
    path = tmp_path/'atomic.db'
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ledger = BudgetLedger.for_connection(conn)
    ledger.initialize()
    policy = Policy(routes=(Route(role='analyst',model_id='fixture/free',tariff=Tariff(input_micro_usd_per_million=0,output_micro_usd_per_million=0)),))
    conn.execute('CREATE TABLE operations(id TEXT PRIMARY KEY)')
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    ledger.create_account('global',policy)
    assert ledger.reserve('global', Request(operation_id='op',role='analyst',model_id='fixture/free',input_tokens=1,output_tokens=1,timeout_seconds=1)).allowed
    conn.execute("INSERT INTO operations VALUES('op')")
    with closing(sqlite3.connect(path)) as other:
        assert other.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0] == 0
        assert other.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0
    conn.rollback()
    assert conn.execute('SELECT COUNT(*) FROM budget_requests').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM budget_accounts').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0
    assert not conn.in_transaction
    conn.close()


def test_fixed_fee_is_paid_even_when_token_rates_are_zero(tmp_path):
    ledger = BudgetLedger(tmp_path/'fees.db')
    ledger.initialize()
    tariff = Tariff(request_micro_usd=1,input_micro_usd_per_million=0,output_micro_usd_per_million=0)
    ledger.create_account('global',Policy(routes=(Route(role='analyst',model_id='fixture/fee',tariff=tariff),)))
    decision = ledger.reserve('global',Request(operation_id='fee',role='analyst',model_id='fixture/fee',input_tokens=1,output_tokens=1,timeout_seconds=1))
    assert not decision.allowed and decision.code == 'paid_approval_required'
    assert tariff.quote(1,1) == 1
