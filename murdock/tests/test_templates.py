import copy

import pytest

from jinja2 import FileSystemLoader, Environment

from .. import TEMPLATES_DIR

from .test_job import test_job

# [
#     {"name": "output.txt"},
#     {"name": "af432-a75fd32-123bacc-v1/", "readable_name": "Eye Candy v1"},
# ],


@pytest.fixture
def jinja_env():
    loader = FileSystemLoader(searchpath=TEMPLATES_DIR)
    return Environment(
        loader=loader,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=True,
    )


@pytest.fixture
def job():
    return copy.deepcopy(test_job)


@pytest.mark.parametrize(
    "summary, failed_builds, failed_tests, artifacts, comment_artifacts, comment_footer",
    [
        pytest.param(
            None,
            None,
            None,
            None,
            [],
            None,
            id="wo_anything",
        ),
        pytest.param(
            {
                "passed": "we passed!",
                "failed": "We failed :(",
                "total": "and my total is...",
            },
            None,
            None,
            None,
            [],
            None,
            id="w_summary",
        ),
        pytest.param(
            {
                "passed": "we passed again!",
                "failed": "We failed here too :(",
                "total": "Totally correct!",
                "runtime_human": "55.3",
                "worker": "test_worker",
            },
            None,
            None,
            None,
            [],
            None,
            id="w_summary_w_runtime_human",
        ),
        pytest.param(
            None,
            [
                {
                    "application": "test_build_app",
                    "target": "test_build_board",
                    "toolchain": "test_build_toolchain",
                    "runtime": "23",
                    "worker": "test_worker",
                }
            ],
            None,
            None,
            [],
            None,
            id="w_failed_builds",
        ),
        pytest.param(
            None,
            None,
            [
                {
                    "application": "test_run_app",
                    "target": "test_run_board",
                    "toolchain": "test_run_toolchain",
                    "runtime": 42.0,
                    "worker": "test_worker",
                }
            ],
            None,
            [],
            None,
            id="w_failed_tests",
        ),
        pytest.param(
            None,
            None,
            None,
            ["test_artifact.txt", "test_artifact_dir/", "another_test_artifact.elf"],
            [],
            None,
            id="w_artifacts_wo_comment_artifacts",
        ),
        pytest.param(
            None,
            None,
            None,
            None,
            [
                {"name": "test_artifact.txt"},
                {"name": "test_artifact_dir/", "readable_name": "Test Artifacts Dir"},
                {"name": "this_is_no_artifact"},
                {
                    "name": "this_is_neither_an_artifact",
                    "readable_name": "I don't know you",
                },
            ],
            None,
            id="wo_artifacts_w_comment_artifacts",
        ),
        pytest.param(
            None,
            None,
            None,
            ["test_artifact.txt", "test_artifact_dir/", "another_test_artifact.elf"],
            [
                {"name": "test_artifact.txt"},
                {"name": "test_artifact_dir/", "readable_name": "Test Artifacts Dir"},
                {"name": "this_is_no_artifact"},
                {
                    "name": "this_is_neither_an_artifact",
                    "readable_name": "I don't know you",
                },
            ],
            None,
            id="w_artifacts",
        ),
        pytest.param(
            None,
            None,
            None,
            None,
            [],
            "I am a comment footer below the comment",
            id="w_comment_footer",
        ),
    ],
)
def test_comment(
    jinja_env,
    job,
    summary,
    failed_builds,
    failed_tests,
    artifacts,
    comment_artifacts,
    comment_footer,
):
    base_url = "http://localhost:8000"
    template = jinja_env.get_template("comment.md.j2")
    context = {"job": job, "base_url": base_url}
    if summary is not None:
        if "runtime_human" in summary:
            job.stop_time = float(summary["runtime_human"])
        job.status.update(summary)
    if failed_tests is not None:
        job.status["failed_tests"] = failed_tests
    if failed_builds is not None:
        job.status["failed_builds"] = failed_builds
    if artifacts is not None:
        job.artifacts = artifacts
    job.config.pr.comment_artifacts = comment_artifacts
    job.config.pr.comment_footer = comment_footer
    rendered_comment = template.render(**context)
    assert job.details_url in rendered_comment
    assert job.commit.sha in rendered_comment
    assert job.commit.message.split("\n")[0] in rendered_comment

    if summary is not None:
        assert summary["passed"] in rendered_comment
        assert summary["failed"] in rendered_comment
        assert summary["total"] in rendered_comment
        if "runtime_human" in summary:
            assert job.runtime_human in rendered_comment
        else:
            assert "| 00s |" in rendered_comment

    if failed_builds is not None:
        for test in failed_builds:
            assert test["application"] in rendered_comment
            assert test["target"] in rendered_comment
            assert test["toolchain"] in rendered_comment
            assert f"{float(test['runtime']):0.2f}" in rendered_comment

    if failed_tests is not None:
        for test in failed_tests:
            assert test["application"] in rendered_comment
            assert test["target"] in rendered_comment
            assert test["toolchain"] in rendered_comment
            assert f"{float(test['runtime']):0.2f}" in rendered_comment

    def assert_artifact(artifact, job, rendered_comment):
        if "readable_name" in artifact:
            assert artifact["readable_name"] in rendered_comment
        else:
            assert f"[{artifact['name']}]" in rendered_comment
        assert f"{base_url}/{job.http_dir}/{artifact['name']}" in rendered_comment

    def assert_not_artifact(artifact, rendered_comment):
        assert artifact["name"] not in rendered_comment
        if "readable_name" in artifact:
            assert artifact["readable_name"] not in rendered_comment

    if artifacts is not None and comment_artifacts:
        for artifact in comment_artifacts:
            if artifact["name"] in artifacts:
                assert_artifact(artifact, job, rendered_comment)
            else:
                assert_not_artifact(artifact, rendered_comment)
        for artifact in artifacts:
            if artifact not in [a["name"] for a in comment_artifacts]:
                # not a comment_artifacts dict, so can't use assert_not_artifact
                assert artifact not in rendered_comment
    elif artifacts is not None:
        for artifact in artifacts:
            # not a comment_artifacts dict, so can't use assert_not_artifact
            assert artifact not in rendered_comment
    else:
        for artifact in comment_artifacts:
            assert_not_artifact(artifact, rendered_comment)

    if comment_footer is not None:
        assert comment_footer in rendered_comment
