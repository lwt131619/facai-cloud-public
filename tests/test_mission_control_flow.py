"""End-to-end flows exercised by the Mission Control console."""

from fastapi.testclient import TestClient

from facai.api import create_app


def make_client(tmp_path):
    return TestClient(create_app(tmp_path))


def create(client, title, steps=('规划', '开发', '测试')):
    response = client.post('/api/v1/missions', json={
        'title': title, 'original_input': f'原始要求：{title}', 'steps': list(steps)})
    assert response.status_code == 201
    return response.json()


def test_console_binding_and_step_flow_is_traceable(tmp_path):
    client = make_client(tmp_path)
    mission = create(client, '库存应用')

    # Console model registration uses api_key_env, never a raw key.
    assert client.put('/api/v1/registries/models/deepseek-flash', json={
        'provider': 'DeepSeek', 'endpoint': 'https://api.deepseek.com',
        'model': 'deepseek-flash', 'api_key_env': 'DEEPSEEK_API_KEY'}).status_code == 200
    models = client.get('/api/v1/registries/models').json()
    assert models['deepseek-flash']['api_key_env'] == 'DEEPSEEK_API_KEY'
    assert 'api_key' not in models['deepseek-flash']

    # Bind the mission to an agent and model; the change is recorded per mission.
    bound = client.put(f"/api/v1/missions/{mission['id']}/binding", json={
        'agent_id': 'opencode', 'model_id': 'deepseek-flash'})
    assert bound.status_code == 200
    assert bound.json()['agent_id'] == 'opencode'
    assert bound.json()['model_id'] == 'deepseek-flash'

    # Steps advance through the real state machine and drive progress.
    first = mission['steps'][0]['id']
    assert client.patch(f"/api/v1/missions/{mission['id']}/steps/{first}",
                        json={'status': 'RUNNING'}).status_code == 200
    assert client.patch(f"/api/v1/missions/{mission['id']}/steps/{first}",
                        json={'status': 'COMPLETED'}).status_code == 200
    detail = client.get(f"/api/v1/missions/{mission['id']}").json()
    assert detail['progress'] == 33

    # Invalid transition is refused rather than silently accepted.
    assert client.patch(f"/api/v1/missions/{mission['id']}/steps/{first}",
                        json={'status': 'RUNNING'}).status_code == 409

    timeline = client.get(f"/api/v1/missions/{mission['id']}/events").json()
    types = [event['type'] for event in timeline]
    assert types[0] == 'MISSION_CREATED'
    assert 'BINDING_CHANGED' in types
    assert types.count('STEP_RUNNING') == 1
    assert types.count('STEP_COMPLETED') == 1
    assert all(event['mission_id'] == mission['id'] for event in timeline)
    step_events = [event for event in timeline if event['step_id'] == first]
    assert {event['type'] for event in step_events} >= {'STEP_RUNNING', 'STEP_COMPLETED'}


def test_binding_rejects_unknown_agent_or_model(tmp_path):
    client = make_client(tmp_path)
    mission = create(client, 'A')
    assert client.put(f"/api/v1/missions/{mission['id']}/binding", json={
        'agent_id': 'ghost', 'model_id': None}).status_code == 422
    assert client.put(f"/api/v1/missions/{mission['id']}/binding", json={
        'agent_id': 'opencode', 'model_id': 'ghost'}).status_code == 422
    # Binding was not partially applied.
    unchanged = client.get(f"/api/v1/missions/{mission['id']}").json()
    assert unchanged['agent_id'] is None and unchanged['model_id'] is None


def test_binding_is_isolated_between_missions(tmp_path):
    client = make_client(tmp_path)
    first, second = create(client, 'A'), create(client, 'B')
    client.put(f"/api/v1/missions/{first['id']}/binding", json={
        'agent_id': 'opencode', 'model_id': 'deepseek-flash'})
    other = client.get(f"/api/v1/missions/{second['id']}").json()
    assert other['agent_id'] is None and other['model_id'] is None
    second_events = client.get(f"/api/v1/missions/{second['id']}/events").json()
    assert all(event['type'] == 'MISSION_CREATED' for event in second_events)


def test_professional_prompt_is_stored_separately_from_original(tmp_path):
    client = make_client(tmp_path)
    mission = create(client, '库存应用')
    original = mission['original_input']

    updated = client.put(f"/api/v1/missions/{mission['id']}/prompt", json={
        'professional_prompt': '为库存管理构建 REST API，包含测试与部署。'})
    assert updated.status_code == 200
    body = updated.json()
    assert body['original_input'] == original          # verbatim input is untouched
    assert body['professional_prompt'].startswith('为库存管理')

    events = client.get(f"/api/v1/missions/{mission['id']}/events").json()
    assert any(event['type'] == 'PROMPT_UPDATED' for event in events)
    # Clearing the derived prompt still keeps the original input.
    cleared = client.put(f"/api/v1/missions/{mission['id']}/prompt", json={}).json()
    assert cleared['professional_prompt'] is None and cleared['original_input'] == original
