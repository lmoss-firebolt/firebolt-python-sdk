from typing import Callable, List

from httpx import codes
from pytest import mark, raises
from pytest_httpx import HTTPXMock

from firebolt.async_db import Connection, connect
from firebolt.async_db._types import ColType
from firebolt.common.exception import (
    ConfigurationError,
    ConnectionClosedError,
    FireboltDatabaseError,
    FireboltEngineError,
)
from firebolt.common.settings import Settings
from firebolt.common.urls import ACCOUNT_ENGINE_BY_NAME_URL


@mark.asyncio
async def test_closed_connection(connection: Connection) -> None:
    """Connection methods are unavailable for closed connection."""
    await connection.aclose()

    with raises(ConnectionClosedError):
        connection.cursor()

    with raises(ConnectionClosedError):
        async with connection:
            pass

    await connection.aclose()


@mark.asyncio
async def test_cursors_closed_on_close(connection: Connection) -> None:
    """Connection closes all its cursors on close."""
    c1, c2 = connection.cursor(), connection.cursor()
    assert (
        len(connection._cursors) == 2
    ), "Invalid number of cursors stored in connection."

    await connection.aclose()
    assert connection.closed, "Connection was not closed on close."
    assert c1.closed, "Cursor was not closed on connection close."
    assert c2.closed, "Cursor was not closed on connection close."
    assert len(connection._cursors) == 0, "Cursors left in connection after close."
    await connection.aclose()


@mark.asyncio
async def test_cursor_initialized(
    settings: Settings,
    db_name: str,
    httpx_mock: HTTPXMock,
    auth_callback: Callable,
    auth_url: str,
    query_callback: Callable,
    query_url: str,
    python_query_data: List[List[ColType]],
) -> None:
    """Connection initialised its cursors properly."""
    httpx_mock.add_callback(auth_callback, url=auth_url)
    httpx_mock.add_callback(query_callback, url=query_url)

    for url in (settings.server, f"https://{settings.server}"):
        async with (
            await connect(
                engine_url=url,
                database=db_name,
                username="u",
                password="p",
                account_name="a",
                api_endpoint=settings.server,
            )
        ) as connection:
            cursor = connection.cursor()
            assert (
                cursor.connection == connection
            ), "Invalid cursor connection attribute."
            assert (
                cursor._client == connection._client
            ), "Invalid cursor _client attribute"

            assert await cursor.execute("select*") == len(python_query_data)

            cursor.close()
            assert (
                cursor not in connection._cursors
            ), "Cursor wasn't removed from connection after close."


@mark.asyncio
async def test_connect_empty_parameters():
    with raises(ConfigurationError):
        async with await connect(engine_url="engine_url"):
            pass


@mark.asyncio
async def test_connect_access_token(
    settings: Settings,
    db_name: str,
    httpx_mock: HTTPXMock,
    auth_callback: Callable,
    auth_url: str,
    check_token_callback: Callable,
    query_url: str,
    python_query_data: List[List[ColType]],
    access_token: str,
):
    httpx_mock.add_callback(check_token_callback, url=query_url)
    async with (
        await connect(
            engine_url=settings.server,
            database=db_name,
            access_token=access_token,
            account_name="a",
            api_endpoint=settings.server,
        )
    ) as connection:
        cursor = connection.cursor()
        assert await cursor.execute("select*") == -1

    with raises(ConfigurationError):
        async with await connect(engine_url="engine_url", database="database"):
            pass

    with raises(ConfigurationError):
        async with await connect(
            engine_url="engine_url",
            database="database",
            username="username",
            password="password",
            access_token="access_token",
        ):
            pass


@mark.asyncio
async def test_connect_engine_name(
    settings: Settings,
    db_name: str,
    httpx_mock: HTTPXMock,
    auth_callback: Callable,
    auth_url: str,
    query_callback: Callable,
    query_url: str,
    account_id_url: str,
    account_id_callback: Callable,
    engine_id: str,
    get_engine_url: str,
    get_engine_callback: Callable,
    python_query_data: List[List[ColType]],
    account_id: str,
):
    """connect properly handles engine_name"""

    with raises(ConfigurationError):
        async with await connect(
            engine_url="engine_url",
            engine_name="engine_name",
            database="db",
            username="username",
            password="password",
            account_name="account_name",
        ):
            pass

    httpx_mock.add_callback(auth_callback, url=auth_url)
    httpx_mock.add_callback(query_callback, url=query_url)
    httpx_mock.add_callback(account_id_callback, url=account_id_url)
    httpx_mock.add_callback(get_engine_callback, url=get_engine_url)

    engine_name = settings.server.split(".")[0]

    # Mock engine id lookup error
    httpx_mock.add_response(
        url=f"https://{settings.server}"
        + ACCOUNT_ENGINE_BY_NAME_URL.format(account_id=account_id)
        + f"?engine_name={engine_name}",
        status_code=codes.NOT_FOUND,
    )

    with raises(FireboltEngineError):
        async with await connect(
            database="db",
            username="username",
            password="password",
            engine_name=engine_name,
            account_name=settings.account_name,
            api_endpoint=settings.server,
        ):
            pass

    # Mock engine id lookup by name
    httpx_mock.add_response(
        url=f"https://{settings.server}"
        + ACCOUNT_ENGINE_BY_NAME_URL.format(account_id=account_id)
        + f"?engine_name={engine_name}",
        status_code=codes.OK,
        json={"engine_id": {"engine_id": engine_id}},
    )

    async with await connect(
        engine_name=engine_name,
        database=db_name,
        username="u",
        password="p",
        account_name=settings.account_name,
        api_endpoint=settings.server,
    ) as connection:
        assert await connection.cursor().execute("select*") == len(python_query_data)


@mark.asyncio
async def test_connect_default_engine(
    settings: Settings,
    db_name: str,
    httpx_mock: HTTPXMock,
    auth_callback: Callable,
    auth_url: str,
    query_callback: Callable,
    query_url: str,
    account_id_url: str,
    account_id_callback: Callable,
    engine_id: str,
    get_engine_url: str,
    get_engine_callback: Callable,
    database_by_name_url: str,
    database_by_name_callback: Callable,
    database_id: str,
    bindings_url: str,
    python_query_data: List[List[ColType]],
    account_id: str,
):
    httpx_mock.add_callback(auth_callback, url=auth_url)
    httpx_mock.add_callback(query_callback, url=query_url)
    httpx_mock.add_callback(account_id_callback, url=account_id_url)
    httpx_mock.add_callback(database_by_name_callback, url=database_by_name_url)
    bindings_url = f"{bindings_url}?filter.id_database_id_eq={database_id}"
    httpx_mock.add_response(
        url=bindings_url,
        status_code=codes.OK,
        json={"edges": []},
    )

    with raises(FireboltDatabaseError):
        async with await connect(
            database=db_name,
            username="u",
            password="p",
            account_name=settings.account_name,
            api_endpoint=settings.server,
        ):
            pass

    httpx_mock.add_response(
        url=bindings_url,
        status_code=codes.OK,
        json={
            "edges": [
                {
                    "engine_is_default": False,
                    "id": {"engine_id": engine_id},
                }
            ]
        },
    )

    with raises(FireboltDatabaseError):
        async with await connect(
            database=db_name,
            username="u",
            password="p",
            account_name=settings.account_name,
            api_endpoint=settings.server,
        ):
            pass

    non_default_engine_id = "non_default_engine_id"

    httpx_mock.add_response(
        url=bindings_url,
        status_code=codes.OK,
        json={
            "edges": [
                {
                    "engine_is_default": False,
                    "id": {"engine_id": non_default_engine_id},
                },
                {
                    "engine_is_default": True,
                    "id": {"engine_id": engine_id},
                },
            ]
        },
    )
    httpx_mock.add_callback(get_engine_callback, url=get_engine_url)
    async with await connect(
        database=db_name,
        username="u",
        password="p",
        account_name=settings.account_name,
        api_endpoint=settings.server,
    ) as connection:
        assert await connection.cursor().execute("select*") == len(python_query_data)


@mark.asyncio
async def test_connection_commit(connection: Connection):
    # nothing happens
    connection.commit()

    await connection.aclose()
    with raises(ConnectionClosedError):
        connection.commit()
