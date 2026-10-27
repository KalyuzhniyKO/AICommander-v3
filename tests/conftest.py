import json
import base64
import hashlib
import socket
import urllib.request
import pytest
from fastapi.testclient import TestClient
from backend.app import config as app_config

@pytest.fixture(autouse=True)
def isolated_app(tmp_path,monkeypatch):
    # Never read the developer's .env, credentials or model config in tests.
    monkeypatch.setattr(app_config,"_dotenv",lambda: None)
    monkeypatch.setenv("PANEL_PASSWORD", "test-panel-password-32-characters-long")
    monkeypatch.setenv("PANEL_USER", "owner")
    monkeypatch.delenv("LOCAL_REVIEW_ROOT", raising=False)
    monkeypatch.delenv("SPEND_POLICY_FILE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY","")
    monkeypatch.setenv("OPENROUTER_BASE_URL","https://models.invalid/api/v1")
    # Never inherit an operator's live OmniRoute opt-in or credentials.
    for key in ("OMNIROUTE_ENABLED", "OMNIROUTE_BASE_URL", "OMNIROUTE_API_KEY",
                "OMNIROUTE_TIMEOUT_SECONDS", "OMNIROUTE_MAX_GET_ATTEMPTS", "OMNIROUTE_MAX_OUTPUT_TOKENS",
                "OMNIROUTE_RUN_LIVE_SMOKE", "OMNIROUTE_SMOKE_INFERENCE", "OMNIROUTE_SMOKE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key in ('HERMES_ENABLED','HERMES_PYTHON','HERMES_SOURCE','HERMES_MODEL','HERMES_EXPECTED_COMMIT','HERMES_TIMEOUT_SECONDS','HERMES_MAX_OUTPUT_TOKENS'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv("DEFAULT_TIMEOUT_SECONDS","1")
    monkeypatch.setenv("MAX_MODEL_RETRIES","1")
    monkeypatch.setenv("DATABASE_URL",f"sqlite:///{tmp_path / 'test.db'}")
    models_path=tmp_path / "models.json"
    monkeypatch.setattr(app_config,"DEFAULT_MODELS_PATH",models_path)
    from backend.app import main as main_app
    monkeypatch.setattr(main_app,"DEFAULT_MODELS_PATH",models_path)
    from backend.app.orchestration import model_check
    monkeypatch.setattr(model_check,"DEFAULT_MODELS_PATH",models_path)
    monkeypatch.setattr(main_app,"settings",app_config.get_settings())

    def deny_network(*args, **kwargs):
        pytest.fail("Real network access is forbidden; mock the provider/transport")

    monkeypatch.setattr(urllib.request,"urlopen",deny_network)
    # Windows asyncio implements socketpair with an internal loopback connection.
    # Provider HTTP calls (including loopback endpoints) remain blocked above.
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guard(original):
        def connect(sock, address):
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original(sock, address)
            return deny_network()
        return connect

    monkeypatch.setattr(socket.socket,"connect",guard(original_connect))
    monkeypatch.setattr(socket.socket,"connect_ex",guard(original_connect_ex))
    monkeypatch.setattr(socket,"create_connection",deny_network)

@pytest.fixture
def client():
    from backend.app import main as main_app
    with TestClient(main_app.app, base_url="http://127.0.0.1", headers={"X-AICommander-Request": "1", "Authorization": "Basic " + base64.b64encode(b"owner:test-panel-password-32-characters-long").decode()}) as c:yield c

@pytest.fixture
def repository(tmp_path):
    from contextlib import closing
    from backend.app.storage.db import connect, init_db
    from backend.app.storage.repositories import Repository
    with closing(connect(tmp_path / "repository.db")) as conn:
        init_db(conn)
        yield Repository(conn)

@pytest.fixture
def write_models_config(tmp_path):
    def write(data):
        path=tmp_path / "models.json"
        path.write_text(data if isinstance(data,str) else json.dumps(data),encoding="utf-8")
        return path
    return write

@pytest.fixture
def minimal_models_config():
    return {r:[f"openrouter/test-{r}"] for r in ("manager","architect","designer","coder","reviewer")}

@pytest.fixture
def saved_review_store(tmp_path, monkeypatch):
    """Published synthetic bundles copied byte-for-byte into an explicit store."""
    from dataclasses import replace
    from pathlib import Path
    import shutil
    from backend.app import main
    root = tmp_path / 'reviews'
    root.mkdir()
    bundles = {}
    for case in ('passing', 'failing'):
        source = Path(__file__).resolve().parents[1] / 'docs' / 'local-run-examples' / case
        manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
        destination = root / manifest['run_id']
        shutil.copytree(source, destination)
        bundles[case] = destination
    monkeypatch.setattr(main, 'settings', replace(main.settings, local_review_root=str(root)))
    return root, bundles

@pytest.fixture
def provider_budget_factory(tmp_path):
    """Explicit trusted-zero fixture policy for transport tests; real ledger, no bypass."""
    from contextlib import ExitStack, closing
    from dataclasses import replace
    import time
    from backend.app.storage.db import connect, init_db
    from backend.app.budgets.runtime import RuntimeSpend, ProviderBudget
    counter = 0
    with ExitStack() as resources:
        def create(provider, model, base_url, *, role='analyst', output_tokens=256, api_key=''):
            nonlocal counter
            counter += 1
            conn = resources.enter_context(closing(connect(tmp_path/f'provider-{counter}.sqlite3')))
            init_db(conn)
            now = time.time_ns()//1_000_000
            from hashlib import sha256
            document = {'schema_version':1,'verified_at_ms':now-1000,'valid_until_ms':now+86399000,
                        'routes':[{'provider':provider,'model_id':model,'base_url':base_url.rstrip('/'),
                                   'roles':[role], 'account_sha256':sha256(api_key.encode()).hexdigest(), 'tariff':{'input_micro_usd_per_million':0,'output_micro_usd_per_million':0},
                                   'fixed_route_verified':True,'billing_categories_verified':True,'provider_caps_verified':True,
                                   'max_output_tokens':output_tokens,'max_payload_bytes':512*1024}]}
            path = tmp_path/f'provider-policy-{counter}.json'
            path.write_text(json.dumps(document),encoding='utf-8')
            settings = replace(app_config.get_settings(),spend_policy_file=str(path))
            if provider=='openrouter':settings=replace(settings,openrouter_api_key=api_key)
            else:settings=replace(settings,omniroute=replace(settings.omniroute,api_key=api_key))
            return ProviderBudget(RuntimeSpend(conn,settings),task_id=None,origin='model_check',role=role)
        yield create

@pytest.fixture
def app_spend_policy(tmp_path, monkeypatch):
    """Synthetic price/cap metadata for the real application runtime, not live tariffs."""
    from dataclasses import replace
    import time
    from backend.app import main
    now=time.time_ns()//1_000_000
    document={'schema_version':1,'verified_at_ms':now-1000,'valid_until_ms':now+86399000,
              'global_ceiling_micro_usd':10000,'project_ceiling_micro_usd':10000,'task_ceiling_micro_usd':10000,
              'routes':[]}
    for model,rate in [('fixture/free',0),('fixture/paid',1000000)]:
        document['routes'].append({'provider':'openrouter','base_url':'https://models.invalid/v1',
            'model_id':model,'roles':['analyst','architect','developer','reviewer'],
            'account_sha256':hashlib.sha256(b'synthetic-not-real-key').hexdigest(),
            'tariff':{'input_micro_usd_per_million':rate,'output_micro_usd_per_million':rate,'request_micro_usd':1 if rate else 0},
            'upstream_tariff':{'input_micro_usd_per_million':rate,'output_micro_usd_per_million':rate,'request_micro_usd':1 if rate else 0},
            'reported_cost_unit':'usd','fixed_route_verified':True,'billing_categories_verified':True,'provider_caps_verified':True,
            'max_input_tokens':100,'max_output_tokens':10,'timeout_seconds':1})
    path=tmp_path/'app-spending-policy.json'
    path.write_text(json.dumps(document),encoding='utf-8')
    monkeypatch.setattr(main,'settings',replace(main.settings,spend_policy_file=str(path),
        openrouter_api_key='synthetic-not-real-key',openrouter_base_url='https://models.invalid/v1'))
    return path,document
