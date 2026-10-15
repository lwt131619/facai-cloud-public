"""Original words stay verbatim; the professional prompt is separate and reviewable."""

from fastapi.testclient import TestClient

from facai.api import create_app

ORIGINAL = "把  库存\t管理\n做成网站，别改我的意思！"


def create(client):
    return client.post('/api/v1/missions', json={
        'title': '库存', 'original_input': ORIGINAL, 'steps': ['开发']}).json()


def set_prompt(client, mission, value):
    return client.put(f"/api/v1/missions/{mission['id']}/prompt",
                      json={'professional_prompt': value})


def test_original_is_byte_for_byte_preserved(tmp_path):
    client = TestClient(create_app(tmp_path))
    mission = create(client)
    assert mission['original_input'] == ORIGINAL

    body = set_prompt(client, mission, '构建一个库存管理 REST API。').json()
    assert body['original_input'] == ORIGINAL
    assert body['professional_prompt'] == '构建一个库存管理 REST API。'


def test_each_prompt_change_is_reviewable_in_the_timeline(tmp_path):
    client = TestClient(create_app(tmp_path))
    mission = create(client)
    set_prompt(client, mission, '第一版专业提示')
    set_prompt(client, mission, '第二版专业提示')

    events = client.get(f"/api/v1/missions/{mission['id']}/events").json()
    audits = [event['payload'] for event in events if event['type'] == 'PROMPT_UPDATED']
    assert [a['professional_prompt'] for a in audits] == ['第一版专业提示', '第二版专业提示']
    assert all(a['original_input'] == ORIGINAL for a in audits)

    current = client.get(f"/api/v1/missions/{mission['id']}").json()
    assert current['original_input'] == ORIGINAL
    assert current['professional_prompt'] == '第二版专业提示'


def test_clearing_the_derived_prompt_keeps_the_original(tmp_path):
    client = TestClient(create_app(tmp_path))
    mission = create(client)
    set_prompt(client, mission, '临时提示')
    cleared = set_prompt(client, mission, None).json()
    assert cleared['professional_prompt'] is None
    assert cleared['original_input'] == ORIGINAL
    events = client.get(f"/api/v1/missions/{mission['id']}/events").json()
    last = [event for event in events if event['type'] == 'PROMPT_UPDATED'][-1]
    assert last['payload']['professional_prompt'] is None
    assert last['payload']['original_input'] == ORIGINAL
