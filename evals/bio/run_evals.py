from evals.bio.cases import BIO_EVAL_CASES, evaluate_tool_selection


def run_bio_evals() -> dict[str, dict]:
    results: dict[str, dict] = {}
    for case in BIO_EVAL_CASES:
        outcome = evaluate_tool_selection(case)
        results[case.name] = {
            "passed": bool(outcome["skills_ok"] and outcome["tools_ok"] and outcome["plan_steps_ok"]),
            **{key: sorted(value) if isinstance(value, set) else value for key, value in outcome.items()},
        }
    return results


if __name__ == "__main__":
    report = run_bio_evals()
    for name, item in report.items():
        print(name, "PASS" if item["passed"] else "FAIL", item)
