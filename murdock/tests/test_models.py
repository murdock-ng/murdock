from datetime import datetime
from datetime import time as dtime

import pytest

from murdock.models import JobQueryModel


test_date_before = datetime.combine(
    datetime.strptime("2021-09-03", "%Y-%m-%d"),
    dtime(hour=23, minute=59, second=59, microsecond=999)
)
test_date_after = datetime.strptime("2021-08-18", "%Y-%m-%d")


@pytest.mark.parametrize(
    "query,result", [
        (JobQueryModel(), {}),
        (JobQueryModel(limit=42), {}),
        (JobQueryModel(uid="12345"), {"uid": "12345"}),
        (JobQueryModel(is_pr=True), {"prinfo": {"$exists": True}}),
        (JobQueryModel(is_pr=False), {"prinfo": {"$exists": False}}),
        (JobQueryModel(is_branch=True), {"ref": {"$regex": "^refs/heads/.*"}}),
        (JobQueryModel(is_branch=False), {
            "ref": {"$not": {"$regex": "^refs/heads/.*"}}
        }),
        (JobQueryModel(is_tag=True), {"ref": {"$regex": "^refs/tags/.*"}}),
        (JobQueryModel(is_tag=False), {
            "ref": {"$not": {"$regex": "^refs/tags/.*"}}
        }),
        (JobQueryModel(prnum=42), {"prinfo.number": 42}),
        (JobQueryModel(branch="test"), {"ref": "refs/heads/test"}),
        (JobQueryModel(tag="test"), {"ref": "refs/tags/test"}),
        (JobQueryModel(ref="refs/heads/test"), {"ref": "refs/heads/test"}),
        (JobQueryModel(ref="refs/tags/test"), {"ref": "refs/tags/test"}),
        (JobQueryModel(sha="abcdef"), {"commit.sha": "abcdef"}),
        (JobQueryModel(author="me"), {"commit.author": "me"}),
        (JobQueryModel(result="invalid"), {}),
        (JobQueryModel(result="passed"), {"result": "passed"}),
        (JobQueryModel(result="errored"), {"result": "errored"}),
        (JobQueryModel(after="2021-08-18"), {
            "since": {"$gte": test_date_after.timestamp()}
        }),
        (JobQueryModel(before="2021-09-03"), {
            "since": {"$lte": test_date_before.timestamp()}
        }),
        (JobQueryModel(before="2021-09-03", after="2021-08-18"), {
            "since": {
                "$lte": test_date_before.timestamp(),
                "$gte": test_date_after.timestamp(),
            }
        }),
        (
            JobQueryModel(
                uid="12345",
                prnum=42,
                sha="abcdef",
                author="me",
                result="passed",
                before="2021-09-03",
                after="2021-08-18",
            ),
            {
                "uid": "12345",
                "prinfo.number": 42,
                "commit.sha": "abcdef",
                "commit.author": "me",
                "result": "passed",
                "since": {
                    "$lte": test_date_before.timestamp(),
                    "$gte": test_date_after.timestamp(),
                }
            }
        ),
    ]
)
def test_job_query_model(query: JobQueryModel, result):
    assert query.to_mongodb_query() == result
