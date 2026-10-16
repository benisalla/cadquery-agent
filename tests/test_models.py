from cadquery_agent.models import DEFAULT_PROFILE, profile_for


def test_profiles_match_by_model_family() -> None:
    assert profile_for("qwen2.5-coder:3b").prefix == "qwen2.5-coder"
    assert profile_for("qwen3.5:4b").vision
    assert profile_for("qwen3:4b-instruct-2507-q4_K_M").no_think
    assert profile_for("hf.co/someone/Qwen2.5VL-gguf:Q4").vision


def test_unknown_models_get_the_default_profile() -> None:
    assert profile_for("my-custom-model") is DEFAULT_PROFILE
