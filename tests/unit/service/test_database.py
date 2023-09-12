from typing import Callable

from pytest import raises
from pytest_httpx import HTTPXMock

from firebolt.model.database import Database
from firebolt.model.engine import Engine
from firebolt.service.manager import ResourceManager
from firebolt.utils.exception import AttachedEngineInUseError


def test_database_create(
    httpx_mock: HTTPXMock,
    resource_manager: ResourceManager,
    database_get_callback: Callable,
    create_databases_callback: Callable,
    system_engine_no_db_query_url: str,
    mock_database: Database,
    mock_engine: Engine,
):
    httpx_mock.add_callback(
        create_databases_callback, url=system_engine_no_db_query_url, method="POST"
    )
    httpx_mock.add_callback(
        database_get_callback, url=system_engine_no_db_query_url, method="POST"
    )

    database = resource_manager.databases.create(
        name=mock_database.name,
        region=mock_database.region,
        attached_engines=[mock_engine],
        description=mock_database.description,
    )

    assert database == mock_database


def test_database_get(
    httpx_mock: HTTPXMock,
    resource_manager: ResourceManager,
    database_get_callback: Callable,
    system_engine_no_db_query_url: str,
    mock_database: Database,
):
    httpx_mock.add_callback(
        database_get_callback, url=system_engine_no_db_query_url, method="POST"
    )

    database = resource_manager.databases.get(mock_database.name)

    assert database == mock_database


def test_database_get_many(
    httpx_mock: HTTPXMock,
    resource_manager: ResourceManager,
    databases_get_callback: Callable,
    system_engine_no_db_query_url: str,
    mock_database: Database,
    mock_database_2: Database,
):
    httpx_mock.add_callback(
        databases_get_callback,
        url=system_engine_no_db_query_url,
        method="POST",
    )

    databases = resource_manager.databases.get_many(
        name_contains=mock_database.name,
        attached_engine_name_eq="mockengine",
        attached_engine_name_contains="mockengine",
        region_eq="us-east-1",
    )

    assert len(databases) == 2
    assert databases[0] == mock_database
    assert databases[1] == mock_database_2


def test_database_update(
    httpx_mock: HTTPXMock,
    resource_manager: ResourceManager,
    database_update_callback: Callable,
    system_engine_no_db_query_url: str,
    mock_database: Database,
):
    httpx_mock.add_callback(
        database_update_callback, url=system_engine_no_db_query_url, method="POST"
    )

    mock_database._service = resource_manager.databases
    database = mock_database.update(description="new description")

    assert database.description == "new description"


def test_database_delete_busy_engine(
    httpx_mock: HTTPXMock,
    resource_manager: ResourceManager,
    system_engine_no_db_query_url: str,
    get_engine_callback_stopping: Engine,
    mock_database: Database,
    instance_type_callback: Callable,
    instance_type_url: str,
):
    httpx_mock.add_callback(instance_type_callback, url=instance_type_url)
    httpx_mock.add_callback(
        get_engine_callback_stopping, url=system_engine_no_db_query_url
    )

    mock_database._service = resource_manager.engines

    with raises(AttachedEngineInUseError):
        mock_database.delete()
