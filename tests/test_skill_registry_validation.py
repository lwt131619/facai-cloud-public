"""Backward-compatible skill metadata validation for the skills registry."""

import pytest

from facai.registries import Registries


def _reg():
    return Registries(store=None)


def _full_skill(overrides=None, key="code-review"):
    base = {
        "name": key,
        "description": "Automated code review skill",
        "source_url": "https://github.com/example/code-review",
        "license": "MIT",
        "review_status": "approved",
        "enabled": True,
    }
    if overrides:
        base.update(overrides)
    return base


class TestLegacySkillBackwardCompat:
    def test_old_format_name_enabled_true_accepted(self):
        # The "Only approved skills can be enabled" ban is gone: legacy
        # entries may carry enabled:true (review_status defaults approved).
        reg = _reg()
        result = reg.register("skills", "reviewer", {"name": "Reviewer", "enabled": True})
        assert result["enabled"] is True
        assert result["review_status"] == "approved"

    def test_old_format_name_only_accepted(self):
        reg = _reg()
        result = reg.register("skills", "reviewer", {"name": "Reviewer"})
        assert result["name"] == "Reviewer"

    def test_old_format_enabled_false_accepted(self):
        reg = _reg()
        result = reg.register("skills", "reviewer", {"name": "Reviewer", "enabled": False})
        assert result["enabled"] is False

    def test_old_format_preserved_in_catalog(self):
        reg = _reg()
        reg.register("skills", "reviewer", {"name": "Reviewer"})
        assert reg.list("skills")["reviewer"]["name"] == "Reviewer"

    def test_old_format_with_description_accepted(self):
        reg = _reg()
        result = reg.register(
            "skills", "reviewer",
            {"name": "Reviewer", "description": "A legacy reviewer skill"},
        )
        assert result["description"] == "A legacy reviewer skill"
        assert "source_url" not in result
        assert "license" not in result

    def test_old_format_description_only_accepted(self):
        reg = _reg()
        result = reg.register(
            "skills", "reviewer",
            {"name": "Reviewer", "description": "A legacy reviewer skill"},
        )
        assert result["description"] == "A legacy reviewer skill"


class TestFullSkillMetadataSuccess:
    def test_approved_skill_with_enabled_true(self):
        reg = _reg()
        result = reg.register("skills", "code-review", _full_skill())
        assert result["review_status"] == "approved"
        assert result["enabled"] is True

    def test_pending_skill_with_enabled_false(self):
        reg = _reg()
        skill = _full_skill({"review_status": "pending", "enabled": False})
        result = reg.register("skills", "code-review", skill)
        assert result["review_status"] == "pending"

    def test_rejected_skill_with_enabled_false(self):
        reg = _reg()
        skill = _full_skill({"review_status": "rejected", "enabled": False})
        result = reg.register("skills", "code-review", skill)
        assert result["review_status"] == "rejected"

    def test_external_skill_defaults_to_approved(self):
        reg = _reg()
        skill = _full_skill()
        del skill["review_status"]
        skill["enabled"] = False
        result = reg.register("skills", "code-review", skill)
        assert result["review_status"] == "approved"

    def test_skill_id_single_word(self):
        reg = _reg()
        result = reg.register("skills", "reviewer", _full_skill(key="reviewer"))
        assert result is not None

    def test_skill_id_with_hyphens(self):
        reg = _reg()
        result = reg.register("skills", "code-review-v2", _full_skill(key="code-review-v2"))
        assert result is not None

    def test_skill_id_with_digits(self):
        reg = _reg()
        result = reg.register("skills", "skill123", _full_skill(key="skill123"))
        assert result is not None

    def test_skill_id_max_length_64(self):
        reg = _reg()
        key = "a" * 64
        result = reg.register("skills", key, _full_skill(key=key))
        assert result is not None

    def test_description_exactly_1024(self):
        reg = _reg()
        skill = _full_skill({"description": "x" * 1024})
        result = reg.register("skills", "code-review", skill)
        assert result is not None

    def test_approved_skill_enabled_false_ok(self):
        reg = _reg()
        skill = _full_skill({"enabled": False})
        result = reg.register("skills", "code-review", skill)
        assert result["enabled"] is False

    def test_https_source_url_accepted(self):
        reg = _reg()
        skill = _full_skill({"source_url": "https://example.com/skill"})
        result = reg.register("skills", "code-review", skill)
        assert result is not None

    def test_source_url_signal_requires_full_metadata(self):
        reg = _reg()
        with pytest.raises(ValueError, match="description"):
            reg.register(
                "skills", "reviewer",
                {"name": "reviewer", "source_url": "https://example.com/skill"},
            )

    def test_source_url_signal_with_description_still_requires_license(self):
        reg = _reg()
        with pytest.raises(ValueError, match="license"):
            reg.register(
                "skills", "reviewer",
                {
                    "name": "reviewer",
                    "description": "An external skill",
                    "source_url": "https://example.com/skill",
                },
            )


class TestFullSkillMetadataRejection:
    def test_leading_hyphen_in_id_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Skill ID"):
            reg.register("skills", "-review", _full_skill())

    def test_trailing_hyphen_in_id_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Skill ID"):
            reg.register("skills", "review-", _full_skill())

    def test_double_hyphen_in_id_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Skill ID"):
            reg.register("skills", "code--review", _full_skill())

    def test_uppercase_in_id_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Skill ID"):
            reg.register("skills", "CodeReview", _full_skill())

    def test_id_too_long_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Skill ID"):
            reg.register("skills", "a" * 65, _full_skill())

    def test_empty_id_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError):
            reg.register("skills", "", _full_skill())

    def test_empty_description_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="description"):
            reg.register("skills", "code-review", _full_skill({"description": ""}))

    def test_whitespace_only_description_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="description"):
            reg.register("skills", "code-review", _full_skill({"description": "   "}))

    def test_description_over_1024_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="1024"):
            reg.register("skills", "code-review", _full_skill({"description": "x" * 1025}))

    def test_http_source_url_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="https"):
            reg.register("skills", "code-review", _full_skill({"source_url": "http://example.com"}))

    def test_missing_source_url_rejected(self):
        reg = _reg()
        skill = _full_skill()
        del skill["source_url"]
        with pytest.raises(ValueError, match="source_url"):
            reg.register("skills", "code-review", skill)

    def test_empty_license_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="license"):
            reg.register("skills", "code-review", _full_skill({"license": ""}))

    def test_missing_license_rejected(self):
        reg = _reg()
        skill = _full_skill()
        del skill["license"]
        with pytest.raises(ValueError, match="license"):
            reg.register("skills", "code-review", skill)

    def test_invalid_review_status_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="review_status"):
            reg.register("skills", "code-review", _full_skill({"review_status": "unknown"}))

    def test_pending_skill_with_enabled_true_accepted(self):
        # enabled:true is accepted with any review_status value now.
        reg = _reg()
        result = reg.register("skills", "code-review", _full_skill({"review_status": "pending", "enabled": True}))
        assert result["review_status"] == "pending"
        assert result["enabled"] is True

    def test_rejected_skill_with_enabled_true_accepted(self):
        reg = _reg()
        result = reg.register("skills", "code-review", _full_skill({"review_status": "rejected", "enabled": True}))
        assert result["review_status"] == "rejected"
        assert result["enabled"] is True

    def test_forbidden_field_raw_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"raw": "data"}))

    def test_forbidden_field_content_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"content": "data"}))

    def test_forbidden_field_instruction_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"instruction": "data"}))

    def test_forbidden_field_body_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"body": "data"}))

    def test_forbidden_field_scripts_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"scripts": "data"}))

    def test_forbidden_field_path_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"path": "/usr/bin"}))

    def test_forbidden_field_api_key_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError):
            reg.register("skills", "code-review", _full_skill({"api_key": "secret"}))

    def test_forbidden_field_on_legacy_skill_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "reviewer", {"name": "Reviewer", "enabled": True, "raw": "data"})

    def test_forbidden_field_body_on_legacy_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "reviewer", {"name": "Reviewer", "body": "instructions"})


class TestSkillNameMustMatchKey:
    def test_name_missing_rejected(self):
        reg = _reg()
        skill = _full_skill()
        del skill["name"]
        with pytest.raises(ValueError, match="name"):
            reg.register("skills", "code-review", skill)

    def test_name_empty_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="name"):
            reg.register("skills", "code-review", _full_skill({"name": ""}))

    def test_name_not_string_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="name"):
            reg.register("skills", "code-review", _full_skill({"name": 123}))

    def test_name_mismatch_with_key_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="match"):
            reg.register("skills", "code-review", _full_skill({"name": "wrong-name"}))

    def test_name_matching_key_accepted(self):
        reg = _reg()
        result = reg.register("skills", "code-review", _full_skill())
        assert result["name"] == "code-review"


class TestSourceUrlStrictValidation:
    def test_bare_https_rejected_no_hostname(self):
        reg = _reg()
        with pytest.raises(ValueError, match="hostname"):
            reg.register("skills", "code-review", _full_skill({"source_url": "https://"}))

    def test_https_with_userinfo_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="user info"):
            reg.register("skills", "code-review", _full_skill({"source_url": "https://user:pass@example.com/skill"}))

    def test_https_with_username_only_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="user info"):
            reg.register("skills", "code-review", _full_skill({"source_url": "https://user@example.com/skill"}))

    def test_ftp_scheme_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="https"):
            reg.register("skills", "code-review", _full_skill({"source_url": "ftp://example.com/skill"}))

    def test_source_url_not_string_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="https URL string"):
            reg.register("skills", "code-review", _full_skill({"source_url": 12345}))

    def test_valid_https_url_with_path_accepted(self):
        reg = _reg()
        skill = _full_skill({"source_url": "https://github.com/org/repo/blob/main/skill.md"})
        result = reg.register("skills", "code-review", skill)
        assert result is not None


class TestReviewStatusTypeValidation:
    def test_review_status_list_raises_valueerror(self):
        reg = _reg()
        with pytest.raises(ValueError, match="review_status"):
            reg.register("skills", "code-review", _full_skill({"review_status": ["pending", "approved"]}))

    def test_review_status_dict_raises_valueerror(self):
        reg = _reg()
        with pytest.raises(ValueError, match="review_status"):
            reg.register("skills", "code-review", _full_skill({"review_status": {"status": "pending"}}))

    def test_review_status_int_raises_valueerror(self):
        reg = _reg()
        with pytest.raises(ValueError, match="review_status"):
            reg.register("skills", "code-review", _full_skill({"review_status": 1}))

    def test_review_status_none_raises_valueerror(self):
        reg = _reg()
        with pytest.raises(ValueError, match="review_status"):
            reg.register("skills", "code-review", _full_skill({"review_status": None}))

    def test_review_status_bool_raises_valueerror(self):
        reg = _reg()
        with pytest.raises(ValueError, match="review_status"):
            reg.register("skills", "code-review", _full_skill({"review_status": True}))


class TestForbiddenContentOverrideFields:
    def test_forbidden_instructions_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"instructions": "do stuff"}))

    def test_forbidden_skill_md_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"skill_md": "# Skill"}))

    def test_forbidden_commands_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"commands": ["run"]}))

    def test_forbidden_code_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"code": "print('hi')"}))

    def test_forbidden_executable_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "code-review", _full_skill({"executable": "/usr/bin/python"}))

    def test_forbidden_instructions_on_legacy_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "reviewer", {"name": "Reviewer", "instructions": "do stuff"})

    def test_forbidden_field_check_is_case_insensitive(self):
        reg = _reg()
        with pytest.raises(ValueError, match="Forbidden"):
            reg.register("skills", "reviewer", {"name": "Reviewer", "Instructions": "run me"})


class TestOtherRegistriesUnchanged:
    def test_agent_register_still_works(self):
        reg = _reg()
        result = reg.register("agents", "custom", {"name": "Custom", "adapter": "test"})
        assert result["name"] == "Custom"

    def test_model_register_still_works(self):
        reg = _reg()
        result = reg.register("models", "custom-model", {"name": "Custom", "provider": "test"})
        assert result["name"] == "Custom"

    def test_runtime_register_still_works(self):
        reg = _reg()
        result = reg.register("runtimes", "custom-rt", {"name": "Custom", "kind": "local"})
        assert result["name"] == "Custom"

    def test_api_key_still_rejected_in_other_registries(self):
        reg = _reg()
        with pytest.raises(ValueError, match="api_key"):
            reg.register("models", "bad", {"name": "Bad", "api_key": "secret"})


class TestReviewStatusDefaultOnAllSkills:
    def test_legacy_name_only_defaults_to_approved(self):
        reg = _reg()
        result = reg.register("skills", "reviewer", {"name": "reviewer"})
        assert result["review_status"] == "approved"

    def test_legacy_disabled_defaults_to_approved(self):
        reg = _reg()
        result = reg.register("skills", "reviewer", {"name": "reviewer", "enabled": False})
        assert result["review_status"] == "approved"

    def test_legacy_description_only_defaults_to_approved(self):
        reg = _reg()
        result = reg.register(
            "skills", "reviewer",
            {"name": "reviewer", "description": "A legacy skill"},
        )
        assert result["review_status"] == "approved"

    def test_full_skill_explicit_pending_gets_pending(self):
        reg = _reg()
        skill = _full_skill({"review_status": "pending", "enabled": False})
        result = reg.register("skills", "code-review", skill)
        assert result["review_status"] == "pending"


class TestEnabledMustBeJsonBool:
    def test_enabled_integer_1_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="boolean"):
            reg.register("skills", "reviewer", {"name": "reviewer", "enabled": 1})

    def test_enabled_string_true_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="boolean"):
            reg.register("skills", "reviewer", {"name": "reviewer", "enabled": "true"})

    def test_enabled_integer_0_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="boolean"):
            reg.register("skills", "reviewer", {"name": "reviewer", "enabled": 0})

    def test_enabled_string_false_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="boolean"):
            reg.register("skills", "reviewer", {"name": "reviewer", "enabled": "false"})

    def test_enabled_yes_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="boolean"):
            reg.register("skills", "reviewer", {"name": "reviewer", "enabled": "yes"})

    def test_enabled_true_bool_accepted_in_full(self):
        reg = _reg()
        result = reg.register("skills", "code-review", _full_skill({"enabled": True}))
        assert result["enabled"] is True

    def test_enabled_false_bool_accepted_in_full(self):
        reg = _reg()
        result = reg.register(
            "skills", "code-review",
            _full_skill({"enabled": False, "review_status": "pending"}),
        )
        assert result["enabled"] is False


class TestApiKeyCasefoldRejection:
    def test_api_key_uppercase_in_skills_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="api_key"):
            reg.register("skills", "reviewer", {"name": "reviewer", "API_KEY": "secret"})

    def test_api_key_mixed_case_in_skills_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="api_key"):
            reg.register("skills", "reviewer", {"name": "reviewer", "Api_Key": "secret"})

    def test_api_key_uppercase_in_models_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="api_key"):
            reg.register("models", "bad", {"name": "Bad", "API_KEY": "secret"})

    def test_api_key_mixed_in_agents_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="api_key"):
            reg.register("agents", "bad", {"name": "Bad", "Api_Key": "secret"})

    def test_api_key_uppercase_in_runtimes_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="api_key"):
            reg.register("runtimes", "bad", {"name": "Bad", "API_KEY": "secret"})


class TestSourceUrlNetworkRestrictions:
    def test_source_url_with_query_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="query"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://example.com/skill?ref=main"}),
            )

    def test_source_url_with_fragment_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="fragment"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://example.com/skill#section"}),
            )

    def test_source_url_with_query_and_fragment_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="query"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://example.com/skill?ref=main#section"}),
            )

    def test_source_url_localhost_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="localhost"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://localhost/skill"}),
            )

    def test_source_url_loopback_ip_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="loopback"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://127.0.0.1/skill"}),
            )

    def test_source_url_private_10_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="private"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://10.0.0.1/skill"}),
            )

    def test_source_url_private_172_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="private"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://172.16.0.1/skill"}),
            )

    def test_source_url_private_192_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="private"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://192.168.1.1/skill"}),
            )

    def test_source_url_ipv6_loopback_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="loopback"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://[::1]/skill"}),
            )

    def test_source_url_reserved_zero_rejected(self):
        reg = _reg()
        with pytest.raises(ValueError, match="reserved"):
            reg.register(
                "skills", "code-review",
                _full_skill({"source_url": "https://0.0.0.0/skill"}),
            )

    def test_source_url_public_domain_accepted(self):
        reg = _reg()
        skill = _full_skill({"source_url": "https://github.com/org/repo"})
        result = reg.register("skills", "code-review", skill)
        assert result is not None

    def test_source_url_without_query_or_fragment_accepted(self):
        reg = _reg()
        skill = _full_skill({"source_url": "https://example.com/org/repo/blob/main/skill.md"})
        result = reg.register("skills", "code-review", skill)
        assert result is not None
