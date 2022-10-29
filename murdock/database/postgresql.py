from typing import Any, List, Optional, Tuple
from datetime import datetime, timedelta, timezone

from murdock.config import DB_CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.models import CommitModel, JobModel, JobQueryModel, PullRequestInfo
from murdock.database import Database

import asyncpg
import orjson as json


class PostgresDatabase(Database):
    JOB_TO_COLUMN = {
        "uid": "uuid",
        "commit.sha": "commit_sha",
        "commit.tree": "commit_tree",
        "commit.message": "commit_message",
        "commit.author": "commit_author",
        "env": "environment",
        "user_env": "user_environment",
    }

    @staticmethod
    def _gen_simple_condition(
        conditions: List[str],
        args: List[str],
        name: str,
        value: Any,
        nargs: int,
        prefix: Optional[str] = None,
        equality="=",
    ) -> int:
        if value is not None:
            conditions.append(f"{name} {equality} ${nargs}")
            if prefix is None:
                args.append(value)
            else:
                args.append(prefix + value)
            nargs += 1
        return nargs

    @staticmethod
    def _bool2not(val: bool) -> str:
        return "NOT" if not val else ""

    @classmethod
    def _gen_condition_clause(
        cls, query: JobQueryModel, nargs=1
    ) -> Tuple[str, List[Any]]:
        conditions: List[str] = []
        args: List[Any] = []
        before = None
        after = None
        if query.before is not None:
            before = datetime.strptime(query.before, "%Y-%m-%d")
        if query.after is not None:
            after = datetime.strptime(query.after, "%Y-%m-%d")
        nargs = cls._gen_simple_condition(conditions, args, "uuid", query.uid, nargs)
        nargs = cls._gen_simple_condition(
            conditions, args, "prinfo_number", query.prnum, nargs
        )
        nargs = cls._gen_simple_condition(
            conditions, args, "ref", query.branch, nargs, prefix="refs/heads/"
        )
        nargs = cls._gen_simple_condition(
            conditions, args, "ref", query.tag, nargs, prefix="refs/tags/"
        )
        nargs = cls._gen_simple_condition(conditions, args, "ref", query.ref, nargs)
        nargs = cls._gen_simple_condition(
            conditions, args, "commit_sha", query.sha, nargs
        )
        nargs = cls._gen_simple_condition(
            conditions, args, "commit_tree", query.tree, nargs
        )
        nargs = cls._gen_simple_condition(
            conditions, args, "commit_author", query.author, nargs
        )
        nargs = cls._gen_simple_condition(
            conditions, args, "creation_time", after, nargs, equality=">"
        )
        nargs = cls._gen_simple_condition(
            conditions, args, "creation_time", before, nargs, equality="<"
        )
        if query.is_branch is not None:
            conditions.append(
                f"{cls._bool2not(query.is_branch)} ref LIKE 'refs/heads/%'"
            )
        if query.is_tag is not None:
            conditions.append(f"{cls._bool2not(query.is_tag)} ref LIKE 'refs/tags%'")
        if query.is_pr is not None:
            conditions.append(f"prinfo IS {cls._bool2not(not query.is_pr)} NULL")
        if query.states is not None:
            conditions.append(f"state = ANY(${nargs}::result_state[])")
            args.append(query.states.split())
            nargs += 1
        if query.prstates is not None:
            conditions.append(f"prinfo_state = ANY(${nargs}::pr_state[])")
            args.append(query.prstates.split())
            nargs += 1
        if len(conditions) == 0:
            return "", []
        return "WHERE " + " AND ".join(conditions), args

    def __init__(self):
        self.db_pool = None

    @staticmethod
    async def _load_extensions(conn):
        await conn.execute(
            """
            CREATE EXTENSION IF NOT EXISTS hstore;
            """
        )

    async def _create_schema(self):
        async with self.db_pool.acquire() as conn:
            await self._load_extensions(conn)

            # Conditionally add new enum types
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'result_state') THEN
                        CREATE TYPE result_state AS ENUM
                            ('queued', 'running', 'passed', 'errored', 'stopped');
                    END IF;
                END
                $$;
                """
            )
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'pr_state') THEN
                        CREATE TYPE pr_state AS ENUM
                            ('open', 'closed');
                    END IF;
                END
                $$;
                """
            )

            # Create main table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs(
                    id serial PRIMARY KEY,
                    uuid UUID UNIQUE NOT NULL,
                    creation_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    start_time TIMESTAMP WITH TIME ZONE,
                    commit_sha TEXT,
                    commit_tree TEXT,
                    commit_message TEXT,
                    commit_author TEXT,
                    prinfo_number INTEGER,
                    prinfo_state pr_state,
                    ref TEXT,
                    output TEXT,
                    output_text_url TEXT,
                    environment hstore,
                    user_environment hstore,
                    prinfo jsonb,
                    fasttracked BOOLEAN,
                    runtime INTERVAL,
                    status jsonb,
                    state result_state,
                    trigger TEXT,
                    triggered_by TEXT,
                    artifacts TEXT[] DEFAULT '{}'::TEXT[]
                    );
                """
            )

            # Create all indexes used with search queries
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS prinfo_number_idx ON jobs (prinfo_number);
                CREATE INDEX IF NOT EXISTS pr_state_idx ON jobs USING HASH (prinfo_state);
                CREATE INDEX IF NOT EXISTS creation_time_idx ON jobs (creation_time);
                CREATE INDEX IF NOT EXISTS ref_idx ON jobs USING HASH (ref);
                CREATE INDEX IF NOT EXISTS ref_branch_idx ON jobs USING HASH ((ref LIKE 'refs/head/%'));
                CREATE INDEX IF NOT EXISTS ref_tag_idx ON jobs USING HASH ((ref LIKE 'refs/tag/%'));
                CREATE INDEX IF NOT EXISTS state_idx ON jobs USING HASH (state);
                CREATE INDEX IF NOT EXISTS commit_sha_idx ON jobs USING HASH (commit_sha);
                CREATE INDEX IF NOT EXISTS commit_tree_idx ON jobs USING HASH (commit_tree);
                CREATE INDEX IF NOT EXISTS commit_author_idx ON jobs USING HASH (commit_author);
                CREATE INDEX IF NOT EXISTS prinfo_idx ON jobs USING HASH ((prinfo IS NOT NULL));
                """
            )

    @staticmethod
    async def _init_conn(conn: asyncpg.Connection):
        # Automatically encode/decode json type objects
        await conn.set_type_codec(
            "jsonb",
            encoder=lambda x: json.dumps(x).decode(encoding="utf-8", errors="strict"),
            decoder=json.loads,
            schema="pg_catalog",
        )
        # Register hstore to dict encoding/decoding
        await conn.set_builtin_type_codec(
            "hstore",
            codec_name="pg_contrib.hstore",
        )

    async def _setup_conn(self, conn: asyncpg.Connection):
        conn.add_termination_listener(self._termination_listener)

    def _termination_listener(self, conn):
        LOGGER.warning(f"Lost connection to PostgreSQL database {conn}")

    async def init(self):
        port = DB_CONFIG.port if DB_CONFIG.port else 5432
        LOGGER.info(
            f"Connecting to postgres on {DB_CONFIG.host}:{port} with {DB_CONFIG.user} on {DB_CONFIG.name}"
        )
        conn_args = {
            "host": DB_CONFIG.host,
            "port": port,
            "user": DB_CONFIG.user,
            "database": DB_CONFIG.name,
            "password": DB_CONFIG.password,
        }

        LOGGER.info("Initializing PostgreSQL database connection")
        # Register extensions before using them in the pool init
        temp_conn = await asyncpg.connect(**conn_args)
        await self._load_extensions(temp_conn)
        await temp_conn.close()

        self.db_pool = await asyncpg.create_pool(
            init=self._init_conn,
            setup=self._setup_conn,
            **conn_args,
            server_settings={
                "jit": "off"
            },  # Current postgres performance is abysmal with jit enabled
        )
        await self._create_schema()

    async def close(self):
        try:
            await self.db_pool.close()
        except AttributeError:
            LOGGER.error("Attempting to close database pool before initializing it")

    async def insert_job(self, job: MurdockJob):
        return await self.insert_job_model(job.model())

    async def insert_job_model(self, job: JobModel):
        prinfo_number = None
        prinfo_state = None
        prinfo = None
        if job.prinfo is not None:
            prinfo = job.prinfo.dict()
            prinfo_number = prinfo.pop("number")
            prinfo_state = prinfo.pop("state")
        runtime = timedelta(seconds=job.runtime) if job.runtime is not None else None

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO jobs(uuid, creation_time, start_time, commit_sha,
                    commit_tree, commit_message, commit_author, prinfo_number, prinfo_state, ref,
                    output, output_text_url, environment, user_environment, prinfo, fasttracked,
                    runtime, status, state, trigger, triggered_by, artifacts)
                    VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22);
                """,
                job.uid,
                datetime.fromtimestamp(job.creation_time, tz=timezone.utc),
                datetime.fromtimestamp(job.start_time, tz=timezone.utc),
                job.commit.sha,
                job.commit.tree,
                job.commit.message,
                job.commit.author,
                prinfo_number,
                prinfo_state,
                job.ref,
                job.output,
                job.output_text_url,
                job.env,
                job.user_env,
                prinfo,
                job.fasttracked,
                runtime,
                job.status,
                job.state,
                job.trigger,
                job.triggered_by,
                job.artifacts,
            )

    @classmethod
    def _commit_from_entry(cls, entry: asyncpg.Record) -> CommitModel:
        return CommitModel(
            sha=entry["commit_sha"],
            tree=entry["commit_tree"],
            message=entry["commit_message"],
            author=entry["commit_author"],
        )

    @classmethod
    def _prinfo_from_entry(cls, entry: asyncpg.Record) -> Optional[PullRequestInfo]:
        if entry["prinfo"] is None:
            return None
        return PullRequestInfo(
            number=entry["prinfo_number"],
            state=entry["prinfo_state"],
            **entry["prinfo"],
        )

    async def find_job(self, uid: str) -> Optional[MurdockJob]:
        async with self.db_pool.acquire() as conn:
            entry = await conn.fetchrow(
                """
                SELECT * FROM jobs WHERE uuid = $1
                """,
                uid,
            )
        if not entry:
            LOGGER.warning(f"Cannot find job matching uid '{uid}'")
            return None
        commit = self._commit_from_entry(entry)
        prinfo = self._prinfo_from_entry(entry)
        ref = entry["ref"]
        return MurdockJob(
            commit, pr=prinfo, ref=ref, user_env=entry["user_environment"]
        )

    async def find_jobs(self, query: JobQueryModel) -> List[JobModel]:
        condition, args = self._gen_condition_clause(query)
        limit_condition = (
            f" LIMIT {int(query.limit)}" if query.limit is not None else ""
        )
        sql_query = (  # nosec - Only uses argument in query (keep it that way!)
            "SELECT * FROM jobs "
            + condition
            + " ORDER BY creation_time DESC"
            + limit_condition
        )

        async with self.db_pool.acquire() as conn:
            entries = await conn.fetch(sql_query, *args)
        return [
            JobModel(
                uid=entry["uuid"].hex,
                commit=self._commit_from_entry(entry),
                ref=entry["ref"],
                prinfo=self._prinfo_from_entry(entry),
                creation_time=entry["creation_time"].timestamp(),
                start_time=entry["start_time"].timestamp(),
                fasttracked=entry["fasttracked"],
                status=entry["status"],
                state=entry["state"],
                output=entry["output"],
                output_text_url=entry["output_text_url"],
                runtime=entry["runtime"].total_seconds(),
                trigger=entry["trigger"],
                triggered_by=entry["triggered_by"],
                env=entry["environment"],
                artifacts=entry["artifacts"],
                user_env=entry["user_environment"],
            )
            for entry in entries
        ]

    @classmethod
    def _to_postgres_field(cls, field: str) -> str:
        return cls.JOB_TO_COLUMN.get(field, field).replace("'", "''")

    async def update_jobs(self, query: JobQueryModel, field: str, value: Any) -> int:
        postgres_field = self._to_postgres_field(field)
        condition, args = self._gen_condition_clause(query, nargs=2)

        sql_query = (  # nosec - postgres_field is protected and the rest is supplied as argument
            f"UPDATE jobs SET {postgres_field} = $1 " + condition
        )
        async with self.db_pool.acquire() as conn:
            count = await conn.execute(sql_query, value, *args)
        return int(count.split()[1])

    async def count_jobs(self, query: JobQueryModel) -> int:
        condition, args = self._gen_condition_clause(query, nargs=1)
        sql_query = (  # nosec - Only static strings
            "SELECT COUNT(1) FROM jobs " + condition
        )
        async with self.db_pool.acquire() as conn:
            count = await conn.fetchval(sql_query, *args)
        return count

    async def delete_jobs(self, query: JobQueryModel):
        condition, args = self._gen_condition_clause(query, nargs=1)
        sql_query = "DELETE FROM jobs " + condition  # nosec - Only static strings
        async with self.db_pool.acquire() as conn:
            await conn.execute(sql_query, *args)
