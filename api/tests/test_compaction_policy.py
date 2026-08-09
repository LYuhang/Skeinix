from vibecanvas_api.agents.middleware.compaction_policy import policy_for, DEFAULT_POLICY


def test_known_types_have_policies():
    assert policy_for("text/plain").aged_form == "reference"
    assert policy_for("text/plain").fresh_k >= 4
    assert policy_for("text/shell").aged_form == "head_tail"
    assert policy_for("text/shell").fresh_k == 1


def test_case_insensitive():
    assert policy_for("TABLE/JSONL") == policy_for("table/jsonl")


def test_unknown_and_none_fall_back_to_default():
    assert policy_for("application/x-weird") is DEFAULT_POLICY
    assert policy_for(None) is DEFAULT_POLICY
    assert DEFAULT_POLICY.aged_form == "reference"


def test_documents_outrank_data_outrank_shell():
    assert policy_for("text/plain").priority > policy_for("table/jsonl").priority
    assert policy_for("table/jsonl").priority > policy_for("text/shell").priority
