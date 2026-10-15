"""The four capability registries bind independently and stay mission-scoped."""

from fastapi.testclient import TestClient

from facai.api import create_app


def client(tmp_path):
    return TestClient(create_app(tmp_path))


def create(app, title):
    return app.post('/api/v1/missions', json={
        'title': title, 'original_input': f'原话 {title}', 'steps': ['开发', '测试']}).json()


def bind(app, mission, **fields):
    return app.put(f"/api/v1/missions/{mission['id']}/binding", json=fields)


def register_catalog(app):
    assert app.put('/api/v1/registries/skills/reviewer',
                   json={'name': 'Reviewer'}).status_code == 200
    assert app.put('/api/v1/registries/runtimes/gpu-eu',
                   json={'name': 'GPU host', 'kind': 'gpu', 'provider': 'runpod',
                         'location': 'eu-west', 'status': 'ready'}).status_code == 200


def test_each_registry_binds_independently_without_clearing_the_others(tmp_path):
    app = client(tmp_path)
    register_catalog(app)
    mission = create(app, 'A')

    # Bind agent + model together.
    body = bind(app, mission, agent_id='opencode', model_id='deepseek-flash').json()
    assert (body['agent_id'], body['model_id']) == ('opencode', 'deepseek-flash')
    assert body['skill_id'] is None and body['runtime_id'] is None

    # Bind a skill alone; agent and model are preserved.
    body = bind(app, mission, skill_id='reviewer').json()
    assert body['skill_id'] == 'reviewer'
    assert body['agent_id'] == 'opencode' and body['model_id'] == 'deepseek-flash'
    assert body['runtime_id'] is None

    # Bind a runtime alone; nothing else changes.
    body = bind(app, mission, runtime_id='gpu-eu').json()
    assert body['runtime_id'] == 'gpu-eu'
    assert body['skill_id'] == 'reviewer'
    assert body['agent_id'] == 'opencode' and body['model_id'] == 'deepseek-flash'

    # Re-binding the agent alone must not clear the other three.
    body = bind(app, mission, agent_id='opencode').json()
    assert (body['model_id'], body['skill_id'], body['runtime_id']) == (
        'deepseek-flash', 'reviewer', 'gpu-eu')

    events = app.get(f"/api/v1/missions/{mission['id']}/events").json()
    binding_events = [event for event in events if event['type'] == 'BINDING_CHANGED']
    assert len(binding_events) == 4
    assert binding_events[-1]['payload']['changed'] == ['agent_id']
    assert binding_events[-1]['payload']['runtime_id'] == 'gpu-eu'


def test_unknown_registry_values_are_rejected_without_partial_binding(tmp_path):
    app = client(tmp_path)
    register_catalog(app)
    mission = create(app, 'A')
    assert bind(app, mission, agent_id='ghost').status_code == 422
    assert bind(app, mission, model_id='ghost').status_code == 422
    assert bind(app, mission, skill_id='ghost').status_code == 422
    assert bind(app, mission, runtime_id='ghost').status_code == 422
    unchanged = app.get(f"/api/v1/missions/{mission['id']}").json()
    assert unchanged['agent_id'] is None and unchanged['model_id'] is None
    assert unchanged['skill_id'] is None and unchanged['runtime_id'] is None


def test_binding_is_isolated_and_persists_across_restart(tmp_path):
    app = client(tmp_path)
    register_catalog(app)
    first, second = create(app, 'A'), create(app, 'B')
    bind(app, first, agent_id='opencode', model_id='deepseek-flash',
         skill_id='reviewer', runtime_id='gpu-eu')

    other = app.get(f"/api/v1/missions/{second['id']}").json()
    assert (other['agent_id'], other['model_id'], other['skill_id'], other['runtime_id']) == (
        None, None, None, None)
    assert all(event['type'] == 'MISSION_CREATED'
               for event in app.get(f"/api/v1/missions/{second['id']}/events").json())

    reopened = client(tmp_path)
    restored = reopened.get(f"/api/v1/missions/{first['id']}").json()
    assert (restored['agent_id'], restored['model_id'], restored['skill_id'],
            restored['runtime_id']) == ('opencode', 'deepseek-flash', 'reviewer', 'gpu-eu')
