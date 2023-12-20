from datetime import date, datetime
from decimal import Decimal
from typing import Any, List

from pytest import fixture, mark, raises

from firebolt.async_db import Binary, Connection, Cursor, OperationalError
from firebolt.async_db.cursor import QueryStatus
from firebolt.common._types import ColType, Column

VALS_TO_INSERT_2 = ",".join(
    [f"({i}, {i-3}, '{val}')" for (i, val) in enumerate(range(4, 1000))]
)
LONG_INSERT = f"INSERT INTO test_tbl VALUES {VALS_TO_INSERT_2}"

CREATE_EXTERNAL_TABLE = """CREATE EXTERNAL TABLE IF NOT EXISTS ex_lineitem (
  l_orderkey              LONG,
  l_partkey               LONG,
  l_suppkey               LONG,
  l_linenumber            INT,
  l_quantity              LONG,
  l_extendedprice         LONG,
  l_discount              LONG,
  l_tax                   LONG,
  l_returnflag            TEXT,
  l_linestatus            TEXT,
  l_shipdate              TEXT,
  l_commitdate            TEXT,
  l_receiptdate           TEXT,
  l_shipinstruct          TEXT,
  l_shipmode              TEXT,
  l_comment               TEXT
)
URL = 's3://firebolt-publishing-public/samples/tpc-h/parquet/lineitem/'
OBJECT_PATTERN = '*.parquet'
TYPE = (PARQUET);"""

CREATE_FACT_TABLE = """CREATE FACT TABLE IF NOT EXISTS lineitem (
-- In this example, these fact table columns
-- map directly to the external table columns.
  l_orderkey              LONG,
  l_partkey               LONG,
  l_suppkey               LONG,
  l_linenumber            INT,
  l_quantity              LONG,
  l_extendedprice         LONG,
  l_discount              LONG,
  l_tax                   LONG,
  l_returnflag            TEXT,
  l_linestatus            TEXT,
  l_shipdate              TEXT,
  l_commitdate            TEXT,
  l_receiptdate           TEXT,
  l_shipinstruct          TEXT,
  l_shipmode              TEXT,
  l_comment               TEXT
)
PRIMARY INDEX
  l_orderkey,
  l_linenumber;
"""


def assert_deep_eq(got: Any, expected: Any, msg: str) -> bool:
    if isinstance(got, list) and isinstance(expected, list):
        all([assert_deep_eq(f, s, msg) for f, s in zip(got, expected)])
    assert (
        type(got) == type(expected) and got == expected
    ), f"{msg}: {got}(got) != {expected}(expected)"


async def status_loop(
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
    status = await cursor.get_status(query_id)
    # get_status() will return NOT_READY until it succeeds or fails.
    while status == start_status or status == QueryStatus.NOT_READY:
        # This only checks to see if a correct response is returned
        status = await cursor.get_status(query_id)
    assert (
        status == final_status
    ), f"Failed {query}. Got {status} rather than {final_status}."


async def test_connect_engine_name(
    connection_engine_name: Connection,
    all_types_query: str,
    all_types_query_description: List[Column],
    all_types_query_response: List[ColType],
    timezone_name: str,
) -> None:
    """Connecting with engine name is handled properly."""
    await test_select(
        connection_engine_name,
        all_types_query,
        all_types_query_description,
        all_types_query_response,
        timezone_name,
    )


async def test_connect_no_engine(
    connection_no_engine: Connection,
    all_types_query: str,
    all_types_query_description: List[Column],
    all_types_query_response: List[ColType],
    timezone_name: str,
) -> None:
    """Connecting with engine name is handled properly."""
    await test_select(
        connection_no_engine,
        all_types_query,
        all_types_query_description,
        all_types_query_response,
        timezone_name,
    )


async def test_select(
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
            await c.execute(f"SET time_zone={timezone_name}") == -1
        ), "Invalid set statment row count"
        # For boolean test
        assert (
            await c.execute(f"SET bool_output_format=postgres") == -1
        ), "Invalid set statment row count"

        assert await c.execute(all_types_query) == 1, "Invalid row count returned"
        assert c.rowcount == 1, "Invalid rowcount value"
        data = await c.fetchall()
        assert len(data) == c.rowcount, "Invalid data length"
        assert_deep_eq(data, all_types_query_response, "Invalid data")
        assert c.description == all_types_query_description, "Invalid description value"
        assert len(data[0]) == len(c.description), "Invalid description length"
        assert len(await c.fetchall()) == 0, "Redundant data returned by fetchall"

        # Different fetch types
        await c.execute(all_types_query)
        assert (
            await c.fetchone() == all_types_query_response[0]
        ), "Invalid fetchone data"
        assert await c.fetchone() is None, "Redundant data returned by fetchone"

        await c.execute(all_types_query)
        assert len(await c.fetchmany(0)) == 0, "Invalid data size returned by fetchmany"
        data = await c.fetchmany()
        assert len(data) == 1, "Invalid data size returned by fetchmany"
        assert_deep_eq(
            data, all_types_query_response, "Invalid data returned by fetchmany"
        )


@mark.skip(reason="V1 query doesn't finish within the timeout")
@mark.slow
@mark.timeout(timeout=600)
async def test_long_query(
    connection: Connection,
) -> None:
    """AWS ALB TCP timeout set to 350; make sure we handle the keepalive correctly."""
    with connection.cursor() as c:
        await c.execute(
            "SELECT checksum(*) FROM GENERATE_SERIES(1, 200000000000)",  # approx 6m runtime
        )
        data = await c.fetchall()
        assert len(data) == 1, "Invalid data size returned by fetchall"


async def test_drop_create(connection: Connection) -> None:
    """Create and drop table/index queries are handled properly."""

    async def test_query(c: Cursor, query: str) -> None:
        await c.execute(query)
        assert c.description == None
        # This is inconsistent, commenting for now
        # assert c.rowcount == -1

    """Create table query is handled properly"""
    with connection.cursor() as c:
        # Cleanup
        await c.execute("DROP JOIN INDEX IF EXISTS test_drop_create_async_db_join_idx")
        await c.execute(
            "DROP AGGREGATING INDEX IF EXISTS test_drop_create_async_db_agg_idx"
        )
        await c.execute("DROP TABLE IF EXISTS test_drop_create_async_tb")
        await c.execute("DROP TABLE IF EXISTS test_drop_create_async_tb_dim")

        # Fact table
        await test_query(
            c,
            "CREATE FACT TABLE test_drop_create_async(id int, sn string null, f float,"
            "d date, dt datetime, b bool, a array(int)) primary index id",
        )

        # Dimension table
        await test_query(
            c,
            "CREATE DIMENSION TABLE test_drop_create_async_dim(id int, sn string null"
            ", f float, d date, dt datetime, b bool, a array(int))",
        )

        # Create join index
        await test_query(
            c,
            "CREATE JOIN INDEX test_db_join_idx ON "
            "test_drop_create_async_dim(id, sn, f)",
        )

        # Create aggregating index
        await test_query(
            c,
            "CREATE AGGREGATING INDEX test_db_agg_idx ON "
            "test_drop_create_async(id, sum(f), count(dt))",
        )

        # Drop join index
        await test_query(c, "DROP JOIN INDEX test_db_join_idx")

        # Drop aggregating index
        await test_query(c, "DROP AGGREGATING INDEX test_db_agg_idx")

        # Test drop once again
        await test_query(c, "DROP TABLE test_drop_create_async")
        await test_query(c, "DROP TABLE IF EXISTS test_drop_create_async")

        await test_query(c, "DROP TABLE test_drop_create_async_dim")
        await test_query(c, "DROP TABLE IF EXISTS test_drop_create_async_dim")


async def test_insert(connection: Connection) -> None:
    """Insert and delete queries are handled properly."""

    async def test_empty_query(c: Cursor, query: str) -> None:
        assert await c.execute(query) == 0, "Invalid row count returned"
        assert c.rowcount == 0, "Invalid rowcount value"
        assert c.description is None, "Invalid description"
        assert await c.fetchone() is None
        assert len(await c.fetchmany()) == 0
        assert len(await c.fetchall()) == 0

    with connection.cursor() as c:
        await c.execute("DROP TABLE IF EXISTS test_insert_async_tb")
        await c.execute(
            "CREATE FACT TABLE test_insert_async_tb(id int, sn string null, f float,"
            "d date, dt datetime, b bool, a array(int)) primary index id"
        )

        await test_empty_query(
            c,
            "INSERT INTO test_insert_async_tb VALUES (1, 'sn', 1.1, '2021-01-01',"
            "'2021-01-01 01:01:01', true, [1, 2, 3])",
        )

        assert (
            await c.execute(
                "SELECT * FROM test_insert_async_tb ORDER BY test_insert_async_tb.id"
            )
            == 1
        ), "Invalid data length in table after insert"

        assert_deep_eq(
            await c.fetchall(),
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


async def test_parameterized_query(connection: Connection) -> None:
    """Query parameters are handled properly."""

    async def test_empty_query(c: Cursor, query: str, params: tuple) -> None:
        assert await c.execute(query, params) == 0, "Invalid row count returned"
        assert c.rowcount == 0, "Invalid rowcount value"
        assert c.description is None, "Invalid description"
        assert await c.fetchone() is None
        assert len(await c.fetchmany()) == 0
        assert len(await c.fetchall()) == 0

    with connection.cursor() as c:
        await c.execute("DROP TABLE IF EXISTS test_tb_async_parameterized")
        await c.execute(
            "CREATE FACT TABLE test_tb_async_parameterized(i int, f float, s string, sn"
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

        await test_empty_query(
            c,
            "INSERT INTO test_tb_async_parameterized VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, '\\?')",
            params,
        )

        # \0 is converted to 0
        params[2] = "text0"

        assert (
            await c.execute("SELECT * FROM test_tb_async_parameterized") == 1
        ), "Invalid data length in table after parameterized insert"

        assert_deep_eq(
            await c.fetchall(),
            [params + ["?"]],
            "Invalid data in table after parameterized insert",
        )


async def test_multi_statement_query(connection: Connection) -> None:
    """Query parameters are handled properly"""

    with connection.cursor() as c:
        await c.execute("DROP TABLE IF EXISTS test_tb_async_multi_statement")
        await c.execute(
            "CREATE FACT TABLE test_tb_async_multi_statement(i int, s string)"
            " primary index i"
        )

        await c.execute(
            "INSERT INTO test_tb_async_multi_statement values (1, 'a'), (2, 'b');"
            "SELECT * FROM test_tb_async_multi_statement;"
            "SELECT * FROM test_tb_async_multi_statement WHERE i <= 1"
        )
        assert c.description is None, "Invalid description"

        assert await c.nextset()

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
            await c.fetchall(),
            [[1, "a"], [2, "b"]],
            "Invalid data in table after parameterized insert",
        )

        assert await c.nextset()

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
            await c.fetchall(),
            [[1, "a"]],
            "Invalid data in table after parameterized insert",
        )

        assert await c.nextset() is None


async def test_set_invalid_parameter(connection: Connection):
    with connection.cursor() as c:
        assert len(c._set_parameters) == 0
        with raises(OperationalError):
            await c.execute("SET some_invalid_parameter = 1")

        assert len(c._set_parameters) == 0


async def test_server_side_async_execution_query(connection: Connection) -> None:
    """Make an sql query and receive an id back."""
    with connection.cursor() as c:
        query_id = await c.execute("SELECT 1", [], async_execution=True)
    assert (
        query_id and type(query_id) is str
    ), "Invalid query id was returned from server-side async query."


@mark.skip(
    reason="Can't get consistently slow queries so fails significant portion of time."
)
async def test_server_side_async_execution_cancel(
    create_server_side_test_table_setup_teardown_async,
) -> None:
    """Test cancel."""
    c = create_server_side_test_table_setup_teardown_async
    await c.execute(LONG_INSERT, async_execution=True)
    # Cancel, then check that status is cancelled.
    await c.cancel(query_id)
    await status_loop(
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
    create_server_side_test_table_setup_teardown_async,
) -> None:
    """
    Test get_status(). Test for three ending conditions: Simply test to see
    that a StatusQuery object is returned. Queries are succeeding too quickly
    to be able to check for specific status states.
    """
    c = create_server_side_test_table_setup_teardown_async
    query_id = await c.execute(LONG_INSERT, async_execution=True)
    await c.get_status(query_id)
    # Commented out assert because I was getting warnig errors about it being
    # always true even when this should be skipping.
    # assert (
    #     type(status) is QueryStatus,
    # ), "get_status() did not return a QueryStatus object."


async def test_bytea_roundtrip(
    connection: Connection,
) -> None:
    """Inserted and than selected bytea value doesn't get corrupted."""
    with connection.cursor() as c:
        await c.execute("DROP TABLE IF EXISTS test_bytea_roundtrip")
        await c.execute(
            "CREATE FACT TABLE test_bytea_roundtrip(id int, b bytea) primary index id"
        )

        data = "bytea_123\n\tヽ༼ຈل͜ຈ༽ﾉ"

        await c.execute(
            "INSERT INTO test_bytea_roundtrip VALUES (1, ?)", (Binary(data),)
        )
        await c.execute("SELECT b FROM test_bytea_roundtrip")

        bytes_data = (await c.fetchone())[0]

        assert (
            bytes_data.decode("utf-8") == data
        ), "Invalid bytea data returned after roundtrip"


@fixture
async def setup_db(connection_no_engine: Connection, use_db_name: str):
    use_db_name = f"{use_db_name}_async"
    with connection_no_engine.cursor() as cursor:
        await cursor.execute(f"CREATE DATABASE {use_db_name}")
        yield
        await cursor.execute(f"DROP DATABASE {use_db_name}")


@mark.xfail(reason="USE DATABASE is not yet available in 1.0 Firebolt")
async def test_use_database(
    setup_db,
    connection_no_engine: Connection,
    use_db_name: str,
    database_name: str,
) -> None:
    test_db_name = f"{use_db_name}_async"
    test_table_name = "verify_use_db_async"
    """Use database works as expected."""
    with connection_no_engine.cursor() as c:
        await c.execute(f"USE DATABASE {test_db_name}")
        assert c.database == test_db_name
        await c.execute(f"CREATE TABLE {test_table_name} (id int)")
        await c.execute(
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_name = '{test_table_name}'"
        )
        assert (await c.fetchone())[0] == test_table_name, "Table was not created"
        # Change DB and verify table is not there
        await c.execute(f"USE DATABASE {database_name}")
        assert c.database == database_name
        await c.execute(
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_name = '{test_table_name}'"
        )
        assert (await c.fetchone()) is None, "Database was not changed"