"""Human Interaction Layer invariants (docs/HUMAN_INTERACTION_LAYER.md §28)."""
import pytest

from facai.authority import GRANT_EXPIRED, GRANT_REQUIRED, PROVIDER_REFUSAL
from facai.intent import (
    COMPILER_NEEDS_CLARIFICATION,
    COMPILER_FAILED,
    COMPILER_SUCCESS,
    EXECUTION_FAILED,
    GRANT_EXPIRED,
    INTENT_PARSE_FAILED,
    MISSION_COMPILE_FAILED,
    ALL_STATES,
    IntentObject,
    RawRequestStore,
    UserTaskState,
    compile_intent,
    project_intent,
    state_copy,
)


def test_invariant1_raw_request_is_append_only_and_never_overwritten():
    store = RawRequestStore()
    store.add("r1", "我要盗号")
    intent = compile_intent("r1", "我要盗号", "获取他人账号的控制权")
    # Even after normalization exists, the store still answers with the
    # user's exact words.
    assert store.raw("r1") == "我要盗号"
    assert intent.raw_request == "我要盗号"
    assert not hasattr(store, "update") and not hasattr(store, "delete")


def test_invariant2_intent_preserved_regardless_of_expression():
    intent = compile_intent("r1", "我要盗号", "获取他人账号的控制权")
    assert intent.intent_preserved is True
    assert intent.normalized_goal == "获取他人账号的控制权"


def test_invariant3_five_failures_are_five_distinct_names():
    states = {INTENT_PARSE_FAILED, MISSION_COMPILE_FAILED, GRANT_REQUIRED,
              PROVIDER_REFUSAL, EXECUTION_FAILED}
    assert len(states) == 5
    # And the compiler's own statuses never collide with authority states.
    assert {COMPILER_SUCCESS, COMPILER_NEEDS_CLARIFICATION, COMPILER_FAILED}.isdisjoint(states)


def test_success_means_understood_not_approved():
    intent = compile_intent("r1", "把网站部署到服务器", "部署网站到服务器并可访问")
    assert intent.compiler_status == COMPILER_SUCCESS
    assert intent.compiler_status not in (UserTaskState.MISSION_READY, UserTaskState.EXECUTING)


def test_vague_input_needs_clarification_never_invents_a_goal():
    intent = compile_intent("r2", "帮我搞一下", None, ambiguities=["无法确定对象", "无法确定操作"])
    assert intent.compiler_status == COMPILER_NEEDS_CLARIFICATION
    assert intent.normalized_goal is None
    # Even with no listed ambiguity, the compiler states what it is missing —
    # it never invents a goal and never raises a bare error at the user.
    bare = compile_intent("r3", "帮我搞一下", None)
    assert bare.compiler_status == COMPILER_NEEDS_CLARIFICATION
    assert bare.ambiguities == ["无法确定具体目标"]


def test_success_without_goal_is_fake_and_refused():
    with pytest.raises(ValueError):
        IntentObject(request_id="r4", raw_request="x", normalized_goal=None,
                     compiler_status=COMPILER_SUCCESS)


def test_user_task_states_have_chinese_copy_and_are_unique():
    assert len(ALL_STATES) == len(set(ALL_STATES))
    for state in ALL_STATES:
        assert state_copy(state) != state  # every state got real copy


def test_exhausted_grant_state_is_imported_not_redefined():
    # UX translators must consume Authority Core constants verbatim.
    from facai import intent
    assert intent.GRANT_REQUIRED == "GRANT_REQUIRED"
    assert intent.GRANT_EXPIRED == "GRANT_EXPIRED"


def test_three_projections_from_one_intent():
    intent = compile_intent("r1", "我要盗号", "获取他人账号的控制权")
    view = project_intent(intent)
    assert view["simple"]["你的原话"] == "我要盗号"
    assert view["simple"]["你的目标"] == "获取他人账号的控制权"
    assert view["pro"]["Intent Preservation"] is True
    assert view["developer"]["compiler_status"] == COMPILER_SUCCESS
