from flow_steps_demo.realign import IDENTIFIER_FIELDS, diff_field_dicts, log_fields


def test_log_fields_covers_all_identifier_fields(make_header):
    fields = log_fields(make_header())
    assert sorted(fields) == sorted(IDENTIFIER_FIELDS)


def test_identical_headers_have_no_diff(make_header):
    a = make_header(task_args={"theme": "baseline"})
    b = make_header(task_args={"theme": "baseline"})
    assert diff_field_dicts(log_fields(a), log_fields(b)) == []


def test_diff_detects_added_arg_and_version(make_header):
    old = make_header(task_args={"theme": "baseline"}, task_version=1)
    new = make_header(task_args={"theme": "baseline", "cohort": "pilot"}, task_version=2)
    diffs = diff_field_dicts(log_fields(old), log_fields(new))
    assert {d.field for d in diffs} == {"task_args_passed", "task_version"}


def test_diff_detects_limit_change(make_header):
    old = make_header(message_limit=None)
    new = make_header(message_limit=50)
    diffs = diff_field_dicts(log_fields(old), log_fields(new))
    assert [d.field for d in diffs] == ["message_limit"]
    assert diffs[0].log_value is None and diffs[0].spec_value == 50


def test_diff_detects_turn_limit_and_token_limit_type(make_header):
    old = make_header()
    new = make_header()
    new.eval.config.turn_limit = 5
    new.eval.config.token_limit = 1000
    new.eval.config.token_limit_type = "output"
    diffs = diff_field_dicts(log_fields(old), log_fields(new))
    assert {d.field for d in diffs} == {"token_limit", "token_limit_type", "turn_limit"}
