import math
from datetime import date, datetime
from decimal import Decimal
from os import environ
from random import randint
from threading import Thread
from typing import Any, Callable, Generator, List

from pytest import mark, raises

from firebolt.async_db.cursor import QueryStatus
from firebolt.client.auth import Auth
from firebolt.common._types import ColType, Column
from firebolt.db import Binary, Connection, Cursor, OperationalError, connect
from tests.integration.conftest import API_ENDPOINT_ENV
from tests.integration.dbapi.utils import assert_deep_eq

VALS_TO_INSERT = ",".join([f"({i},'{val}')" for (i, val) in enumerate(range(1, 360))])
LONG_INSERT = f"INSERT INTO test_tbl VALUES {VALS_TO_INSERT}"


def assert_deep_eq(got: Any, expected: Any, msg: str) -> bool:
    if type(got) == list and type(expected) == list:
        all([assert_deep_eq(f, s, msg) for f, s in zip(got, expected)])
    assert (
        type(got) == type(expected) and got == expected
    ), f"{msg}: {got}(got) != {expected}(expected)"


def status_loop(
    query_id: str,
    query: str,
    cursor: Cursor,
    start_status: QueryStatus = QueryStatus.NOT_READY,
    final_status: QueryStatus = QueryStatus.ENDED_SUCCESSFULLY,
) -> None:
    """
    Continually check status of asynchronously executed query. Compares
    QueryStatus object returned from get_status() to desired final_status.
    Used in test_server_side_async_execution_cancel() and
    test_server_side_async_execution_get_status().
    """
    status = cursor.get_status(query_id)
    # get_status() will return NOT_READY until it succeeds or fails.
    while status == start_status or status == QueryStatus.NOT_READY:
        # This only checks to see if a correct response is returned
        status = cursor.get_status(query_id)
    assert (
        status == final_status
    ), f"Failed {query}. Got {status} rather than {final_status}."


def test_connect_no_db(
    connection_no_db: Connection,
    all_types_query: str,
    all_types_query_description: List[Column],
    all_types_query_response: List[ColType],
    timezone_name: str,
) -> None:
    """Connecting with engine name is handled properly."""
    test_select(
        connection_no_db,
        all_types_query,
        all_types_query_description,
        all_types_query_response,
        timezone_name,
    )


def test_select(
    connection: Connection,
    all_types_query: str,
    all_types_query_description: List[Column],
    all_types_query_response: List[ColType],
    timezone_name: str,
) -> None:
    """Select handles all data types properly."""
    with connection.cursor() as c:
        # For timestamptz test
        assert (
            c.execute(f"SET time_zone={timezone_name}") == -1
        ), "Invalid set statment row count"
        # For boolean test
        assert (
            c.execute(f"SET bool_output_format=postgres") == -1
        ), "Invalid set statment row count"

        assert c.execute(all_types_query) == 1, "Invalid row count returned"
        assert c.rowcount == 1, "Invalid rowcount value"
        data = c.fetchall()
        assert len(data) == c.rowcount, "Invalid data length"
        assert_deep_eq(data, all_types_query_response, "Invalid data")
        assert c.description == all_types_query_description, "Invalid description value"
        assert len(data[0]) == len(c.description), "Invalid description length"
        assert len(c.fetchall()) == 0, "Redundant data returned by fetchall"

        # Different fetch types
        c.execute(all_types_query)
        assert c.fetchone() == all_types_query_response[0], "Invalid fetchone data"
        assert c.fetchone() is None, "Redundant data returned by fetchone"

        c.execute(all_types_query)
        assert len(c.fetchmany(0)) == 0, "Invalid data size returned by fetchmany"
        data = c.fetchmany()
        assert len(data) == 1, "Invalid data size returned by fetchmany"
        assert_deep_eq(
            data, all_types_query_response, "Invalid data returned by fetchmany"
        )


def test_select_inf(connection: Connection) -> None:
    with connection.cursor() as c:
        c.execute("SELECT 'inf'::float, '-inf'::float")
        data = c.fetchall()
        assert len(data) == 1, "Invalid data size returned by fetchall"
        assert data[0][0] == float("inf"), "Invalid data returned by fetchall"
        assert data[0][1] == float("-inf"), "Invalid data returned by fetchall"


def test_select_nan(connection: Connection) -> None:
    with connection.cursor() as c:
        c.execute("SELECT 'nan'::float, '-nan'::float")
        data = c.fetchall()
        assert len(data) == 1, "Invalid data size returned by fetchall"
        assert math.isnan(data[0][0]), "Invalid data returned by fetchall"
        assert math.isnan(data[0][1]), "Invalid data returned by fetchall"


@mark.slow
@mark.timeout(timeout=550)
def test_long_query(
    connection: Connection,
    minimal_time: Callable[[float], None],
) -> None:
    """AWS ALB TCP timeout set to 350; make sure we handle the keepalive correctly."""

    minimal_time(350)

    with connection.cursor() as c:
        c.execute(
            "SELECT checksum(*) FROM GENERATE_SERIES(1, 200000000000)",  # approx 6m runtime
        )
        data = c.fetchall()
        assert len(data) == 1, "Invalid data size returned by fetchall"


def test_drop_create(connection: Connection) -> None:
    """Create and drop table/index queries are handled properly."""

    def test_query(c: Cursor, query: str) -> None:
        c.execute(query)
        assert c.description == None
        assert c.rowcount == 0

    """Create table query is handled properly"""
    with connection.cursor() as c:
        # Cleanup
        c.execute("DROP AGGREGATING INDEX IF EXISTS test_drop_create_db_agg_idx")
        c.execute("DROP TABLE IF EXISTS test_drop_create_tb")
        c.execute("DROP TABLE IF EXISTS test_drop_create_tb_dim")

        # Fact table
        test_query(
            c,
            "CREATE FACT TABLE test_drop_create_tb(id int, sn string null, f float,"
            "d date, dt datetime, b bool, a array(int)) primary index id",
        )

        # Dimension table
        test_query(
            c,
            "CREATE DIMENSION TABLE test_drop_create_tb_dim(id int, sn string null"
            ", f float, d date, dt datetime, b bool, a array(int))",
        )

        # Create aggregating index
        test_query(
            c,
            "CREATE AGGREGATING INDEX test_drop_create_db_agg_idx ON "
            "test_drop_create_tb(id, sum(f), count(dt))",
        )

        # Drop aggregating index
        test_query(c, "DROP AGGREGATING INDEX test_drop_create_db_agg_idx")

        # Test drop once again
        test_query(c, "DROP TABLE test_drop_create_tb")
        test_query(c, "DROP TABLE IF EXISTS test_drop_create_tb")

        test_query(c, "DROP TABLE test_drop_create_tb_dim")
        test_query(c, "DROP TABLE IF EXISTS test_drop_create_tb_dim")


def test_insert(connection: Connection) -> None:
    """Insert and delete queries are handled properly."""

    def test_empty_query(c: Cursor, query: str) -> None:
        assert c.execute(query) == 0, "Invalid row count returned"
        assert c.rowcount == 0, "Invalid rowcount value"
        assert c.description is None, "Invalid description"
        assert c.fetchone() is None
        assert len(c.fetchmany()) == 0
        assert len(c.fetchall()) == 0

    with connection.cursor() as c:
        c.execute("DROP TABLE IF EXISTS test_insert_tb")
        c.execute(
            "CREATE FACT TABLE test_insert_tb(id int, sn string null, f float,"
            "d date, dt datetime, b bool, a array(int)) primary index id"
        )

        test_empty_query(
            c,
            "INSERT INTO test_insert_tb VALUES (1, 'sn', 1.1, '2021-01-01',"
            "'2021-01-01 01:01:01', true, [1, 2, 3])",
        )

        assert (
            c.execute("SELECT * FROM test_insert_tb ORDER BY test_insert_tb.id") == 1
        ), "Invalid data length in table after insert"

        assert_deep_eq(
            c.fetchall(),
            [
                [
                    1,
                    "sn",
                    1.1,
                    date(2021, 1, 1),
                    datetime(2021, 1, 1, 1, 1, 1),
                    True,
                    [1, 2, 3],
                ],
            ],
            "Invalid data in table after insert",
        )


def test_parameterized_query(connection: Connection) -> None:
    """Query parameters are handled properly."""

    def test_empty_query(c: Cursor, query: str, params: tuple) -> None:
        assert c.execute(query, params) == 0, "Invalid row count returned"
        assert c.rowcount == 0, "Invalid rowcount value"
        assert c.description is None, "Invalid description"
        assert c.fetchone() is None
        assert len(c.fetchmany()) == 0
        assert len(c.fetchall()) == 0

    with connection.cursor() as c:
        c.execute("DROP TABLE IF EXISTS test_tb_parameterized")
        c.execute(
            "CREATE FACT TABLE test_tb_parameterized(i int, f float, s string, sn"
            " string null, d date, dt datetime, b bool, a array(int), "
            "dec decimal(38, 3), ss string) primary index i",
        )

        params = [
            1,
            1.123,
            "text\0",
            None,
            date(2022, 1, 1),
            datetime(2022, 1, 1, 1, 1, 1),
            True,
            [1, 2, 3],
            Decimal("123.456"),
        ]

        test_empty_query(
            c,
            "INSERT INTO test_tb_parameterized VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " '\\?')",
            params,
        )

        # \0 is converted to 0
        params[2] = "text\\0"

        assert (
            c.execute("SELECT * FROM test_tb_parameterized") == 1
        ), "Invalid data length in table after parameterized insert"

        assert_deep_eq(
            c.fetchall(),
            [params + ["\\?"]],
            "Invalid data in table after parameterized insert",
        )


def test_multi_statement_query(connection: Connection) -> None:
    """Query parameters are handled properly"""

    with connection.cursor() as c:
        c.execute("DROP TABLE IF EXISTS test_tb_multi_statement")
        c.execute(
            "CREATE FACT TABLE test_tb_multi_statement(i int, s string) primary index i"
        )

        c.execute(
            "INSERT INTO test_tb_multi_statement values (1, 'a'), (2, 'b');"
            "SELECT * FROM test_tb_multi_statement;"
            "SELECT * FROM test_tb_multi_statement WHERE i <= 1"
        )
        assert c.description is None, "Invalid description"

        assert c.nextset()

        assert c.rowcount == 2, "Invalid select row count"
        assert_deep_eq(
            c.description,
            [
                Column("i", int, None, None, None, None, None),
                Column("s", str, None, None, None, None, None),
            ],
            "Invalid select query description",
        )

        assert_deep_eq(
            c.fetchall(),
            [[1, "a"], [2, "b"]],
            "Invalid data in table after parameterized insert",
        )

        assert c.nextset()

        assert c.rowcount == 1, "Invalid select row count"
        assert_deep_eq(
            c.description,
            [
                Column("i", int, None, None, None, None, None),
                Column("s", str, None, None, None, None, None),
            ],
            "Invalid select query description",
        )

        assert_deep_eq(
            c.fetchall(),
            [[1, "a"]],
            "Invalid data in table after parameterized insert",
        )

        assert c.nextset() is None


def test_set_invalid_parameter(connection: Connection):
    with connection.cursor() as c:
        assert len(c._set_parameters) == 0
        with raises(OperationalError):
            c.execute("set some_invalid_parameter = 1")

        assert len(c._set_parameters) == 0


# Run test multiple times since the issue is flaky
@mark.parametrize("_", range(5))
def test_anyio_backend_import_issue(
    engine_name: str,
    database_name: str,
    auth: Auth,
    account_name: str,
    api_endpoint: str,
    _: int,
) -> None:
    threads_cnt = 3
    requests_cnt = 8
    # collect threads exceptions in an array because they're ignored otherwise
    exceptions = []

    def run_query(idx: int):
        nonlocal auth, database_name, engine_name, account_name, api_endpoint
        try:
            with connect(
                auth=auth,
                database=database_name,
                account_name=account_name,
                engine_name=engine_name,
                api_endpoint=api_endpoint,
            ) as c:
                cursor = c.cursor()
                cursor.execute(f"select {idx}")
        except BaseException as e:
            exceptions.append(e)

    def run_queries_parallel() -> None:
        nonlocal requests_cnt
        threads = [Thread(target=run_query, args=(i,)) for i in range(requests_cnt)]
        [t.start() for t in threads]
        [t.join() for t in threads]

    threads = [Thread(target=run_queries_parallel) for _ in range(threads_cnt)]

    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(exceptions) == 0, exceptions


def test_server_side_async_execution_query(connection: Connection) -> None:
    """Make an sql query and receive an id back."""
    with connection.cursor() as c:
        query_id = c.execute("SELECT 1", [], async_execution=True)
    assert (
        query_id and type(query_id) is str
    ), "Invalid query id was returned from server-side async query."


@mark.skip(
    reason="Can't get consistently slow queries so fails significant portion of time."
)
async def test_server_side_async_execution_cancel(
    create_server_side_test_table_setup_teardown,
) -> None:
    """Test cancel()."""
    c = create_server_side_test_table_setup_teardown
    # Cancel, then check that status is cancelled.
    c.cancel(query_id)
    status_loop(
        query_id,
        "cancel",
        c,
        start_status=QueryStatus.STARTED_EXECUTION,
        final_status=QueryStatus.CANCELED_EXECUTION,
    )


@mark.skip(
    reason=(
        "Can't get consistently slow queries so fails significant portion of time. "
        "get_status() always returns a QueryStatus object, so this assertion will "
        "always pass. Error condition of invalid status is caught in get_status()."
    )
)
async def test_server_side_async_execution_get_status(
    create_server_side_test_table_setup_teardown,
) -> None:
    """Test get_status()."""
    c = create_server_side_test_table_setup_teardown
    query_id = c.execute(LONG_INSERT, async_execution=True)
    status = c.get_status(query_id)
    # Commented out assert because I was getting warnig errors about it being
    # always true even when this should be skipping.
    # assert (
    #     type(status) is QueryStatus,
    # ), "get_status() did not return a QueryStatus object."


@mark.xdist_group("multi_thread_connection_sharing")
def test_multi_thread_connection_sharing(
    engine_name: str,
    database_name: str,
    auth: Auth,
    account_name: str,
    api_endpoint: str,
) -> None:
    """
    Test to verify sharing the same connection between different
    threads works. With asyncio synching an async function this used
    to fail due to a different loop having exclusive rights to the
    Httpx client. Trio fixes this issue.
    """

    exceptions = []

    connection = connect(
        auth=auth,
        database=database_name,
        account_name=account_name,
        engine_name=engine_name,
        api_endpoint=api_endpoint,
    )

    def run_query():
        try:
            cursor = connection.cursor()
            cursor.execute("select 1")
            cursor.fetchall()
        except BaseException as e:
            exceptions.append(e)

    thread_1 = Thread(target=run_query)
    thread_2 = Thread(target=run_query)

    thread_1.start()
    thread_1.join()
    thread_2.start()
    thread_2.join()

    connection.close()
    assert not exceptions


def test_bytea_roundtrip(
    connection: Connection,
) -> None:
    """Inserted and than selected bytea value doesn't get corrupted."""
    with connection.cursor() as c:
        # Set standard_conforming_strings to 0 to allow bytea escape sequences
        # FIR-30650
        c.execute("SET standard_conforming_strings=0")
        c.execute("DROP TABLE IF EXISTS test_bytea_roundtrip")
        c.execute(
            "CREATE FACT TABLE test_bytea_roundtrip(id int, b bytea) primary index id"
        )

        data = "bytea_123\n\tヽ༼ຈل͜ຈ༽ﾉ"

        c.execute("INSERT INTO test_bytea_roundtrip VALUES (1, ?)", (Binary(data),))
        c.execute("SELECT b FROM test_bytea_roundtrip")

        bytes_data = (c.fetchone())[0]
        c.execute("SET standard_conforming_strings=1")
        assert (
            bytes_data.decode("utf-8") == data
        ), "Invalid bytea data returned after roundtrip"


@mark.xfail("dev" not in environ[API_ENDPOINT_ENV], reason="Only works on dev")
def test_use_database(
    setup_v2_db,
    connection_system_engine: Connection,
    database_name: str,
) -> None:
    test_table_name = "verify_use_db"
    """Use database works as expected."""
    with connection_system_engine.cursor() as c:
        c.execute(f"USE DATABASE {setup_v2_db}")
        assert c.database == setup_v2_db
        c.execute(f"CREATE TABLE {test_table_name} (id int)")
        c.execute(
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_name = '{test_table_name}'"
        )
        assert c.fetchone()[0] == test_table_name, "Table was not created"
        # Change DB and verify table is not there
        c.execute(f"USE DATABASE {database_name}")
        assert c.database == database_name
        c.execute(
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_name = '{test_table_name}'"
        )
        assert c.fetchone() is None, "Database was not changed"


def test_account_v2_connection_with_db(
    setup_v2_db: Generator,
    auth: Auth,
    account_name_v2: str,
    api_endpoint: str,
) -> None:
    with connect(
        database=setup_v2_db,
        auth=auth,
        account_name=account_name_v2,
        api_endpoint=api_endpoint,
    ) as connection:
        # This fails if we're not running with a db context
        connection.cursor().execute("SELECT * FROM information_schema.tables LIMIT 1")


def test_account_v2_connection_with_db_and_engine(
    setup_v2_db: Generator,
    connection_system_engine_v2: Connection,
    auth: Auth,
    account_name_v2: str,
    api_endpoint: str,
    engine_v2: str,
) -> None:
    system_cursor = connection_system_engine_v2.cursor()
    # We can only connect to a running engine so start it first
    # via the system connection to keep test isolated
    system_cursor.execute(f"START ENGINE {engine_v2}")
    with connect(
        database=setup_v2_db,
        engine_name=engine_v2,
        auth=auth,
        account_name=account_name_v2,
        api_endpoint=api_endpoint,
    ) as connection:
        # generate a random string to avoid name conflicts
        rnd_suffix = str(randint(0, 1000))
        cursor = connection.cursor()
        cursor.execute(f"CREATE TABLE test_table_{rnd_suffix} (id int)")
        # This fails if we're not running on a user engine
        cursor.execute(f"INSERT INTO test_table_{rnd_suffix} VALUES (1)")