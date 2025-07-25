#  Licensed to the Apache Software Foundation (ASF) under one
#  or more contributor license agreements.  See the NOTICE file
#  distributed with this work for additional information
#  regarding copyright ownership.  The ASF licenses this file
#  to you under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance
#  with the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing,
#  software distributed under the License is distributed on an
#  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#  KIND, either express or implied.  See the License for the
#  specific language governing permissions and limitations
#  under the License.
# pylint: disable=redefined-outer-name,unused-argument
import base64
import os
from typing import Any, Callable, Dict, cast
from unittest import mock

import pytest
from requests_mock import Mocker

import pyiceberg
from pyiceberg.catalog import PropertiesUpdateSummary, load_catalog
from pyiceberg.catalog.rest import OAUTH2_SERVER_URI, SNAPSHOT_LOADING_MODE, RestCatalog
from pyiceberg.exceptions import (
    AuthorizationExpiredError,
    NamespaceAlreadyExistsError,
    NamespaceNotEmptyError,
    NoSuchIdentifierError,
    NoSuchNamespaceError,
    NoSuchTableError,
    NoSuchViewError,
    OAuthError,
    ServerError,
    TableAlreadyExistsError,
)
from pyiceberg.io import load_file_io
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.table.metadata import TableMetadataV1
from pyiceberg.table.sorting import SortField, SortOrder
from pyiceberg.transforms import IdentityTransform, TruncateTransform
from pyiceberg.typedef import RecursiveDict
from pyiceberg.utils.config import Config

TEST_URI = "https://iceberg-test-catalog/"
TEST_CREDENTIALS = "client:secret"
TEST_OAUTH2_SERVER_URI = "https://auth-endpoint/"
TEST_TOKEN = "some_jwt_token"
TEST_SCOPE = "openid_offline_corpds_ds_profile"
TEST_AUDIENCE = "test_audience"
TEST_RESOURCE = "test_resource"

TEST_HEADERS = {
    "Content-type": "application/json",
    "X-Client-Version": "0.14.1",
    "User-Agent": f"PyIceberg/{pyiceberg.__version__}",
    "Authorization": f"Bearer {TEST_TOKEN}",
    "X-Iceberg-Access-Delegation": "vended-credentials",
}
OAUTH_TEST_HEADERS = {
    "Content-type": "application/x-www-form-urlencoded",
}


@pytest.fixture
def example_table_metadata_with_snapshot_v1_rest_json(example_table_metadata_with_snapshot_v1: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "metadata-location": "s3://warehouse/database/table/metadata/00001-5f2f8166-244c-4eae-ac36-384ecdec81fc.gz.metadata.json",
        "metadata": example_table_metadata_with_snapshot_v1,
        "config": {
            "client.factory": "io.tabular.iceberg.catalog.TabularAwsClientFactory",
            "region": "us-west-2",
        },
    }


@pytest.fixture
def example_table_metadata_with_no_location(example_table_metadata_with_snapshot_v1: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "metadata": example_table_metadata_with_snapshot_v1,
        "config": {
            "client.factory": "io.tabular.iceberg.catalog.TabularAwsClientFactory",
            "region": "us-west-2",
        },
    }


@pytest.fixture
def example_table_metadata_no_snapshot_v1_rest_json(example_table_metadata_no_snapshot_v1: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "metadata-location": "s3://warehouse/database/table/metadata.json",
        "metadata": example_table_metadata_no_snapshot_v1,
        "config": {
            "client.factory": "io.tabular.iceberg.catalog.TabularAwsClientFactory",
            "region": "us-west-2",
        },
    }


@pytest.fixture
def rest_mock(requests_mock: Mocker) -> Mocker:
    """Takes the default requests_mock and adds the config endpoint to it

    This endpoint is called when initializing the rest catalog
    """
    requests_mock.get(
        f"{TEST_URI}v1/config",
        json={"defaults": {}, "overrides": {}},
        status_code=200,
    )
    return requests_mock


def test_no_uri_supplied() -> None:
    with pytest.raises(KeyError):
        RestCatalog("production")


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_200(rest_mock: Mocker) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={
            "access_token": TEST_TOKEN,
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "scope": "openid offline",
            "refresh_token": "refresh_token",
        },
        status_code=200,
        request_headers=OAUTH_TEST_HEADERS,
    )
    assert (
        RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS)._session.headers["Authorization"]  # pylint: disable=W0212
        == f"Bearer {TEST_TOKEN}"
    )


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_200_without_optional_fields(rest_mock: Mocker) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={
            "access_token": TEST_TOKEN,
            "token_type": "Bearer",
        },
        status_code=200,
        request_headers=OAUTH_TEST_HEADERS,
    )
    assert (
        RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS)._session.headers["Authorization"]  # pylint: disable=W0212
        == f"Bearer {TEST_TOKEN}"
    )


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_with_optional_oauth_params(rest_mock: Mocker) -> None:
    mock_request = rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={
            "access_token": TEST_TOKEN,
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        status_code=200,
        request_headers=OAUTH_TEST_HEADERS,
    )
    assert (
        RestCatalog(
            "rest", uri=TEST_URI, credential=TEST_CREDENTIALS, audience=TEST_AUDIENCE, resource=TEST_RESOURCE
        )._session.headers["Authorization"]
        == f"Bearer {TEST_TOKEN}"
    )
    assert TEST_AUDIENCE in mock_request.last_request.text
    assert TEST_RESOURCE in mock_request.last_request.text


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_with_optional_oauth_params_as_empty(rest_mock: Mocker) -> None:
    mock_request = rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={
            "access_token": TEST_TOKEN,
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        status_code=200,
        request_headers=OAUTH_TEST_HEADERS,
    )
    assert (
        RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS, audience="", resource="")._session.headers["Authorization"]
        == f"Bearer {TEST_TOKEN}"
    )
    assert TEST_AUDIENCE not in mock_request.last_request.text
    assert TEST_RESOURCE not in mock_request.last_request.text


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_with_default_scope(rest_mock: Mocker) -> None:
    mock_request = rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={
            "access_token": TEST_TOKEN,
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        status_code=200,
        request_headers=OAUTH_TEST_HEADERS,
    )
    assert (
        RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS)._session.headers["Authorization"] == f"Bearer {TEST_TOKEN}"
    )
    assert "catalog" in mock_request.last_request.text


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_with_custom_scope(rest_mock: Mocker) -> None:
    mock_request = rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={
            "access_token": TEST_TOKEN,
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        status_code=200,
        request_headers=OAUTH_TEST_HEADERS,
    )
    assert (
        RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS, scope=TEST_SCOPE)._session.headers["Authorization"]
        == f"Bearer {TEST_TOKEN}"
    )
    assert TEST_SCOPE in mock_request.last_request.text


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_200_w_oauth2_server_uri(rest_mock: Mocker) -> None:
    rest_mock.post(
        TEST_OAUTH2_SERVER_URI,
        json={
            "access_token": TEST_TOKEN,
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        status_code=200,
        request_headers=OAUTH_TEST_HEADERS,
    )
    # pylint: disable=W0212
    assert (
        RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS, **{OAUTH2_SERVER_URI: OAUTH2_SERVER_URI})._session.headers[
            "Authorization"
        ]
        == f"Bearer {TEST_TOKEN}"
    )
    # pylint: enable=W0212


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_config_200(requests_mock: Mocker) -> None:
    requests_mock.get(
        f"{TEST_URI}v1/config",
        json={"defaults": {}, "overrides": {}},
        status_code=200,
    )
    requests_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={
            "access_token": TEST_TOKEN,
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        status_code=200,
        request_headers=OAUTH_TEST_HEADERS,
    )
    RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS, warehouse="s3://some-bucket")

    assert requests_mock.called
    assert requests_mock.call_count == 2

    history = requests_mock.request_history
    assert history[1].method == "GET"
    assert history[1].url == "https://iceberg-test-catalog/v1/config?warehouse=s3%3A%2F%2Fsome-bucket"


def test_properties_sets_headers(requests_mock: Mocker) -> None:
    requests_mock.get(
        f"{TEST_URI}v1/config",
        json={"defaults": {}, "overrides": {}},
        status_code=200,
    )

    catalog = RestCatalog(
        "rest",
        uri=TEST_URI,
        warehouse="s3://some-bucket",
        **{"header.Content-Type": "application/vnd.api+json", "header.Customized-Header": "some/value"},
    )

    assert catalog._session.headers.get("Content-type") == "application/json", (
        "Expected 'Content-Type' default header not to be overwritten"
    )
    assert requests_mock.last_request.headers["Content-type"] == "application/json", (
        "Config request did not include expected 'Content-Type' header"
    )

    assert catalog._session.headers.get("Customized-Header") == "some/value", (
        "Expected 'Customized-Header' header to be 'some/value'"
    )
    assert requests_mock.last_request.headers["Customized-Header"] == "some/value", (
        "Config request did not include expected 'Customized-Header' header"
    )


def test_config_sets_headers(requests_mock: Mocker) -> None:
    namespace = "leden"
    requests_mock.get(
        f"{TEST_URI}v1/config",
        json={
            "defaults": {"header.Content-Type": "application/vnd.api+json", "header.Customized-Header": "some/value"},
            "overrides": {},
        },
        status_code=200,
    )
    requests_mock.post(f"{TEST_URI}v1/namespaces", json={"namespace": [namespace], "properties": {}}, status_code=200)
    catalog = RestCatalog("rest", uri=TEST_URI, warehouse="s3://some-bucket")
    catalog.create_namespace(namespace)

    assert catalog._session.headers.get("Content-type") == "application/json", (
        "Expected 'Content-Type' default header not to be overwritten"
    )
    assert requests_mock.last_request.headers["Content-type"] == "application/json", (
        "Create namespace request did not include expected 'Content-Type' header"
    )

    assert catalog._session.headers.get("Customized-Header") == "some/value", (
        "Expected 'Customized-Header' header to be 'some/value'"
    )
    assert requests_mock.last_request.headers["Customized-Header"] == "some/value", (
        "Create namespace request did not include expected 'Customized-Header' header"
    )


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_400(rest_mock: Mocker) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={"error": "invalid_client", "error_description": "Credentials for key invalid_key do not match"},
        status_code=400,
        request_headers=OAUTH_TEST_HEADERS,
    )

    with pytest.raises(OAuthError) as e:
        RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS)
    assert str(e.value) == "invalid_client: Credentials for key invalid_key do not match"


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
def test_token_401(rest_mock: Mocker) -> None:
    message = "invalid_client"
    rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={"error": "invalid_client", "error_description": "Unknown or invalid client"},
        status_code=401,
        request_headers=OAUTH_TEST_HEADERS,
    )

    with pytest.raises(OAuthError) as e:
        RestCatalog("rest", uri=TEST_URI, credential=TEST_CREDENTIALS)
    assert message in str(e.value)


def test_list_tables_200(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/{namespace}/tables",
        json={"identifiers": [{"namespace": ["examples"], "name": "fooshare"}]},
        status_code=200,
        request_headers=TEST_HEADERS,
    )

    assert RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).list_tables(namespace) == [("examples", "fooshare")]


def test_list_tables_200_sigv4(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/{namespace}/tables",
        json={"identifiers": [{"namespace": ["examples"], "name": "fooshare"}]},
        status_code=200,
        request_headers=TEST_HEADERS,
    )

    assert RestCatalog("rest", **{"uri": TEST_URI, "token": TEST_TOKEN, "rest.sigv4-enabled": "true"}).list_tables(namespace) == [
        ("examples", "fooshare")
    ]
    assert rest_mock.called


def test_list_tables_404(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/{namespace}/tables",
        json={
            "error": {
                "message": "Namespace does not exist: personal in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NoSuchNamespaceException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    with pytest.raises(NoSuchNamespaceError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).list_tables(namespace)
    assert "Namespace does not exist" in str(e.value)


def test_list_views_200(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/{namespace}/views",
        json={"identifiers": [{"namespace": ["examples"], "name": "fooshare"}]},
        status_code=200,
        request_headers=TEST_HEADERS,
    )

    assert RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).list_views(namespace) == [("examples", "fooshare")]


def test_list_views_200_sigv4(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/{namespace}/views",
        json={"identifiers": [{"namespace": ["examples"], "name": "fooshare"}]},
        status_code=200,
        request_headers=TEST_HEADERS,
    )

    assert RestCatalog("rest", **{"uri": TEST_URI, "token": TEST_TOKEN, "rest.sigv4-enabled": "true"}).list_views(namespace) == [
        ("examples", "fooshare")
    ]
    assert rest_mock.called


def test_list_views_404(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/{namespace}/views",
        json={
            "error": {
                "message": "Namespace does not exist: personal in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NoSuchNamespaceException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    with pytest.raises(NoSuchNamespaceError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).list_views(namespace)
    assert "Namespace does not exist" in str(e.value)


def test_view_exists_204(rest_mock: Mocker) -> None:
    namespace = "examples"
    view = "some_view"
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/{namespace}/views/{view}",
        status_code=204,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    assert catalog.view_exists((namespace, view))


def test_view_exists_404(rest_mock: Mocker) -> None:
    namespace = "examples"
    view = "some_view"
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/{namespace}/views/{view}",
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    assert not catalog.view_exists((namespace, view))


def test_view_exists_multilevel_namespace_404(rest_mock: Mocker) -> None:
    multilevel_namespace = "core.examples.some_namespace"
    view = "some_view"
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/{multilevel_namespace}/views/{view}",
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    assert not catalog.view_exists((multilevel_namespace, view))


def test_list_namespaces_200(rest_mock: Mocker) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces",
        json={"namespaces": [["default"], ["examples"], ["fokko"], ["system"]]},
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    assert RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).list_namespaces() == [
        ("default",),
        ("examples",),
        ("fokko",),
        ("system",),
    ]


def test_list_namespace_with_parent_200(rest_mock: Mocker) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces?parent=accounting",
        json={"namespaces": [["accounting", "tax"]]},
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    assert RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).list_namespaces(("accounting",)) == [
        ("accounting", "tax"),
    ]


def test_list_namespace_with_parent_404(rest_mock: Mocker) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces?parent=some_namespace",
        json={
            "error": {
                "message": "Namespace provided in the `parent` query parameter is not found",
                "type": "NoSuchNamespaceException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )

    with pytest.raises(NoSuchNamespaceError):
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).list_namespaces(("some_namespace",))


@pytest.mark.filterwarnings(
    "ignore:Deprecated in 0.8.0, will be removed in 1.0.0. Iceberg REST client is missing the OAuth2 server URI:DeprecationWarning"
)
@pytest.mark.parametrize("status_code", [401, 419])
def test_list_namespaces_token_expired_success_on_retries(rest_mock: Mocker, status_code: int) -> None:
    new_token = "new_jwt_token"
    new_header = dict(TEST_HEADERS)
    new_header["Authorization"] = f"Bearer {new_token}"

    namespaces = rest_mock.register_uri(
        "GET",
        f"{TEST_URI}v1/namespaces",
        [
            {
                "status_code": status_code,
                "json": {
                    "error": {
                        "message": "Authorization expired.",
                        "type": "AuthorizationExpiredError",
                        "code": status_code,
                    }
                },
                "headers": TEST_HEADERS,
            },
            {
                "status_code": 200,
                "json": {"namespaces": [["default"], ["examples"], ["fokko"], ["system"]]},
                "headers": new_header,
            },
            {
                "status_code": 200,
                "json": {"namespaces": [["default"], ["examples"], ["fokko"], ["system"]]},
                "headers": new_header,
            },
        ],
    )
    tokens = rest_mock.post(
        f"{TEST_URI}v1/oauth/tokens",
        json={
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        status_code=200,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN, credential=TEST_CREDENTIALS)
    # LegacyOAuth2AuthManager is created twice through `_create_session()`
    # which results in the token being refreshed twice when the RestCatalog is initialized.
    assert tokens.call_count == 2

    assert catalog.list_namespaces() == [
        ("default",),
        ("examples",),
        ("fokko",),
        ("system",),
    ]
    assert namespaces.call_count == 2
    assert tokens.call_count == 3

    assert catalog.list_namespaces() == [
        ("default",),
        ("examples",),
        ("fokko",),
        ("system",),
    ]
    assert namespaces.call_count == 3
    assert tokens.call_count == 3


def test_create_namespace_200(rest_mock: Mocker) -> None:
    namespace = "leden"
    rest_mock.post(
        f"{TEST_URI}v1/namespaces",
        json={"namespace": [namespace], "properties": {}},
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).create_namespace(namespace)


def test_create_namespace_if_exists_409(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.post(
        f"{TEST_URI}v1/namespaces",
        json={
            "error": {
                "message": "Namespace already exists: fokko in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "AlreadyExistsException",
                "code": 409,
            }
        },
        status_code=409,
        request_headers=TEST_HEADERS,
    )

    RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).create_namespace_if_not_exists(namespace)


def test_create_namespace_409(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.post(
        f"{TEST_URI}v1/namespaces",
        json={
            "error": {
                "message": "Namespace already exists: fokko in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "AlreadyExistsException",
                "code": 409,
            }
        },
        status_code=409,
        request_headers=TEST_HEADERS,
    )
    with pytest.raises(NamespaceAlreadyExistsError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).create_namespace(namespace)
    assert "Namespace already exists" in str(e.value)


def test_drop_namespace_404(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/{namespace}",
        json={
            "error": {
                "message": "Namespace does not exist: leden in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NoSuchNamespaceException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    with pytest.raises(NoSuchNamespaceError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_namespace(namespace)
    assert "Namespace does not exist" in str(e.value)


def test_drop_namespace_409(rest_mock: Mocker) -> None:
    namespace = "examples"
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/{namespace}",
        json={
            "error": {
                "message": "Namespace is not empty: leden in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NamespaceNotEmptyError",
                "code": 409,
            }
        },
        status_code=409,
        request_headers=TEST_HEADERS,
    )
    with pytest.raises(NamespaceNotEmptyError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_namespace(namespace)
    assert "Namespace is not empty" in str(e.value)


def test_load_namespace_properties_200(rest_mock: Mocker) -> None:
    namespace = "leden"
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/{namespace}",
        json={"namespace": ["fokko"], "properties": {"prop": "yes"}},
        status_code=204,
        request_headers=TEST_HEADERS,
    )
    assert RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).load_namespace_properties(namespace) == {"prop": "yes"}


def test_load_namespace_properties_404(rest_mock: Mocker) -> None:
    namespace = "leden"
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/{namespace}",
        json={
            "error": {
                "message": "Namespace does not exist: fokko22 in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NoSuchNamespaceException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    with pytest.raises(NoSuchNamespaceError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).load_namespace_properties(namespace)
    assert "Namespace does not exist" in str(e.value)


def test_update_namespace_properties_200(rest_mock: Mocker) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/properties",
        json={"removed": [], "updated": ["prop"], "missing": ["abc"]},
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    response = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).update_namespace_properties(
        ("fokko",), {"abc"}, {"prop": "yes"}
    )

    assert response == PropertiesUpdateSummary(removed=[], updated=["prop"], missing=["abc"])


def test_namespace_exists_200(rest_mock: Mocker) -> None:
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/fokko",
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)

    assert catalog.namespace_exists("fokko")


def test_namespace_exists_204(rest_mock: Mocker) -> None:
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/fokko",
        status_code=204,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)

    assert catalog.namespace_exists("fokko")


def test_namespace_exists_404(rest_mock: Mocker) -> None:
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/fokko",
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)

    assert not catalog.namespace_exists("fokko")


def test_namespace_exists_500(rest_mock: Mocker) -> None:
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/fokko",
        status_code=500,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)

    with pytest.raises(ServerError):
        catalog.namespace_exists("fokko")


def test_update_namespace_properties_404(rest_mock: Mocker) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/properties",
        json={
            "error": {
                "message": "Namespace does not exist: does_not_exists in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NoSuchNamespaceException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    with pytest.raises(NoSuchNamespaceError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).update_namespace_properties(("fokko",), {"abc"}, {"prop": "yes"})
    assert "Namespace does not exist" in str(e.value)


def test_load_table_200(rest_mock: Mocker, example_table_metadata_with_snapshot_v1_rest_json: Dict[str, Any]) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/fokko/tables/table",
        json=example_table_metadata_with_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    actual = catalog.load_table(("fokko", "table"))
    expected = Table(
        identifier=("fokko", "table"),
        metadata_location=example_table_metadata_with_snapshot_v1_rest_json["metadata-location"],
        metadata=TableMetadataV1(**example_table_metadata_with_snapshot_v1_rest_json["metadata"]),
        io=load_file_io(),
        catalog=catalog,
    )
    # First compare the dicts
    assert actual.metadata.model_dump() == expected.metadata.model_dump()
    assert actual == expected


def test_load_table_200_loading_mode(
    rest_mock: Mocker, example_table_metadata_with_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/fokko/tables/table?snapshots=refs",
        json=example_table_metadata_with_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN, **{SNAPSHOT_LOADING_MODE: "refs"})
    actual = catalog.load_table(("fokko", "table"))
    expected = Table(
        identifier=("fokko", "table"),
        metadata_location=example_table_metadata_with_snapshot_v1_rest_json["metadata-location"],
        metadata=TableMetadataV1(**example_table_metadata_with_snapshot_v1_rest_json["metadata"]),
        io=load_file_io(),
        catalog=catalog,
    )
    # First compare the dicts
    assert actual.metadata.model_dump() == expected.metadata.model_dump()
    assert actual == expected


def test_load_table_honor_access_delegation(
    rest_mock: Mocker, example_table_metadata_with_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    test_headers_with_remote_signing = {**TEST_HEADERS, "X-Iceberg-Access-Delegation": "remote-signing"}
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/fokko/tables/table",
        json=example_table_metadata_with_snapshot_v1_rest_json,
        status_code=200,
        request_headers=test_headers_with_remote_signing,
    )
    # catalog = RestCatalog("rest", **{"uri": TEST_URI, "token": TEST_TOKEN, "access-delegation": "remote-signing"})
    catalog = RestCatalog(
        "rest",
        **{
            "uri": TEST_URI,
            "token": TEST_TOKEN,
            "header.X-Iceberg-Access-Delegation": "remote-signing",
        },
    )
    actual = catalog.load_table(("fokko", "table"))
    expected = Table(
        identifier=("fokko", "table"),
        metadata_location=example_table_metadata_with_snapshot_v1_rest_json["metadata-location"],
        metadata=TableMetadataV1(**example_table_metadata_with_snapshot_v1_rest_json["metadata"]),
        io=load_file_io(),
        catalog=catalog,
    )
    # First compare the dicts
    assert actual.metadata.model_dump() == expected.metadata.model_dump()
    assert actual == expected


def test_load_table_from_self_identifier_200(
    rest_mock: Mocker, example_table_metadata_with_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/pdames/tables/table",
        json=example_table_metadata_with_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    table = catalog.load_table(("pdames", "table"))
    actual = catalog.load_table(table.name())
    expected = Table(
        identifier=("pdames", "table"),
        metadata_location=example_table_metadata_with_snapshot_v1_rest_json["metadata-location"],
        metadata=TableMetadataV1(**example_table_metadata_with_snapshot_v1_rest_json["metadata"]),
        io=load_file_io(),
        catalog=catalog,
    )
    assert actual.metadata.model_dump() == expected.metadata.model_dump()
    assert actual == expected


def test_load_table_404(rest_mock: Mocker) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/fokko/tables/does_not_exists",
        json={
            "error": {
                "message": "Table does not exist: examples.does_not_exists in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NoSuchNamespaceErrorException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )

    with pytest.raises(NoSuchTableError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).load_table(("fokko", "does_not_exists"))
    assert "Table does not exist" in str(e.value)


def test_table_exists_200(rest_mock: Mocker) -> None:
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/fokko/tables/table",
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    assert catalog.table_exists(("fokko", "table"))


def test_table_exists_204(rest_mock: Mocker) -> None:
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/fokko/tables/table",
        status_code=204,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    assert catalog.table_exists(("fokko", "table"))


def test_table_exists_404(rest_mock: Mocker) -> None:
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/fokko/tables/table",
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    assert not catalog.table_exists(("fokko", "table"))


def test_table_exists_500(rest_mock: Mocker) -> None:
    rest_mock.head(
        f"{TEST_URI}v1/namespaces/fokko/tables/table",
        status_code=500,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)

    with pytest.raises(ServerError):
        catalog.table_exists(("fokko", "table"))


def test_drop_table_404(rest_mock: Mocker) -> None:
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/fokko/tables/does_not_exists",
        json={
            "error": {
                "message": "Table does not exist: fokko.does_not_exists in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NoSuchNamespaceErrorException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )

    with pytest.raises(NoSuchTableError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_table(("fokko", "does_not_exists"))
    assert "Table does not exist" in str(e.value)


def test_create_table_200(
    rest_mock: Mocker, table_schema_simple: Schema, example_table_metadata_no_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/tables",
        json=example_table_metadata_no_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    actual = catalog.create_table(
        identifier=("fokko", "fokko2"),
        schema=table_schema_simple,
        location=None,
        partition_spec=PartitionSpec(
            PartitionField(source_id=1, field_id=1000, transform=TruncateTransform(width=3), name="id"), spec_id=1
        ),
        sort_order=SortOrder(SortField(source_id=2, transform=IdentityTransform())),
        properties={"owner": "fokko"},
    )
    expected = Table(
        identifier=("fokko", "fokko2"),
        metadata_location=example_table_metadata_no_snapshot_v1_rest_json["metadata-location"],
        metadata=TableMetadataV1(**example_table_metadata_no_snapshot_v1_rest_json["metadata"]),
        io=load_file_io(),
        catalog=catalog,
    )
    assert actual == expected


def test_create_table_with_given_location_removes_trailing_slash_200(
    rest_mock: Mocker, table_schema_simple: Schema, example_table_metadata_no_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/tables",
        json=example_table_metadata_no_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    location = "s3://warehouse/database/table-custom-location"
    catalog.create_table(
        identifier=("fokko", "fokko2"),
        schema=table_schema_simple,
        location=f"{location}/",
        partition_spec=PartitionSpec(
            PartitionField(source_id=1, field_id=1000, transform=TruncateTransform(width=3), name="id"), spec_id=1
        ),
        sort_order=SortOrder(SortField(source_id=2, transform=IdentityTransform())),
        properties={"owner": "fokko"},
    )
    assert rest_mock.last_request
    assert rest_mock.last_request.json()["location"] == location


def test_create_staged_table_200(
    rest_mock: Mocker,
    table_schema_simple: Schema,
    example_table_metadata_with_no_location: Dict[str, Any],
    example_table_metadata_no_snapshot_v1_rest_json: Dict[str, Any],
) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/tables",
        json=example_table_metadata_with_no_location,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/tables/fokko2",
        json=example_table_metadata_no_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    identifier = ("fokko", "fokko2")
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    txn = catalog.create_table_transaction(
        identifier=identifier,
        schema=table_schema_simple,
        location=None,
        partition_spec=PartitionSpec(
            PartitionField(source_id=1, field_id=1000, transform=TruncateTransform(width=3), name="id"), spec_id=1
        ),
        sort_order=SortOrder(SortField(source_id=2, transform=IdentityTransform())),
        properties={"owner": "fokko"},
    )
    txn.commit_transaction()

    actual_response = rest_mock.last_request.json()
    expected = {
        "identifier": {"namespace": ["fokko"], "name": "fokko2"},
        "requirements": [{"type": "assert-create"}],
        "updates": [
            {"action": "assign-uuid", "uuid": "b55d9dda-6561-423a-8bfc-787980ce421f"},
            {"action": "upgrade-format-version", "format-version": 1},
            {
                "action": "add-schema",
                "schema": {
                    "type": "struct",
                    "fields": [
                        {"id": 1, "name": "id", "type": "int", "required": False},
                        {"id": 2, "name": "data", "type": "string", "required": False},
                    ],
                    "schema-id": 0,
                    "identifier-field-ids": [],
                },
                "last-column-id": 2,
            },
            {"action": "set-current-schema", "schema-id": -1},
            {"action": "add-spec", "spec": {"spec-id": 0, "fields": []}},
            {"action": "set-default-spec", "spec-id": -1},
            {"action": "add-sort-order", "sort-order": {"order-id": 0, "fields": []}},
            {"action": "set-default-sort-order", "sort-order-id": -1},
            {"action": "set-location", "location": "s3://warehouse/database/table"},
            {"action": "set-properties", "updates": {"owner": "bryan", "write.metadata.compression-codec": "gzip"}},
        ],
    }
    assert actual_response == expected


def test_create_table_409(rest_mock: Mocker, table_schema_simple: Schema) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/tables",
        json={
            "error": {
                "message": "Table already exists: fokko.already_exists in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "AlreadyExistsException",
                "code": 409,
            }
        },
        status_code=409,
        request_headers=TEST_HEADERS,
    )

    with pytest.raises(TableAlreadyExistsError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).create_table(
            identifier=("fokko", "fokko2"),
            schema=table_schema_simple,
            location=None,
            partition_spec=PartitionSpec(
                PartitionField(source_id=1, field_id=1000, transform=TruncateTransform(width=3), name="id")
            ),
            sort_order=SortOrder(SortField(source_id=2, transform=IdentityTransform())),
            properties={"owner": "fokko"},
        )
    assert "Table already exists" in str(e.value)


def test_create_table_if_not_exists_200(
    rest_mock: Mocker, table_schema_simple: Schema, example_table_metadata_no_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    def json_callback() -> Callable[[Any, Any], Dict[str, Any]]:
        call_count = 0

        def callback(request: Any, context: Any) -> Dict[str, Any]:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                context.status_code = 200
                return example_table_metadata_no_snapshot_v1_rest_json
            else:
                context.status_code = 409
                return {
                    "error": {
                        "message": "Table already exists: fokko.already_exists in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                        "type": "AlreadyExistsException",
                        "code": 409,
                    }
                }

        return callback

    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/tables",
        json=json_callback(),
        request_headers=TEST_HEADERS,
    )
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/fokko/tables/fokko2",
        json=example_table_metadata_no_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    table1 = catalog.create_table(
        identifier=("fokko", "fokko2"),
        schema=table_schema_simple,
        location=None,
        partition_spec=PartitionSpec(
            PartitionField(source_id=1, field_id=1000, transform=TruncateTransform(width=3), name="id"), spec_id=1
        ),
        sort_order=SortOrder(SortField(source_id=2, transform=IdentityTransform())),
        properties={"owner": "fokko"},
    )
    table2 = catalog.create_table_if_not_exists(
        identifier=("fokko", "fokko2"),
        schema=table_schema_simple,
        location=None,
        partition_spec=PartitionSpec(PartitionField(source_id=1, field_id=1000, transform=TruncateTransform(width=3), name="id")),
        sort_order=SortOrder(SortField(source_id=2, transform=IdentityTransform())),
        properties={"owner": "fokko"},
    )
    assert table1 == table2


def test_create_table_419(rest_mock: Mocker, table_schema_simple: Schema) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/fokko/tables",
        json={
            "error": {
                "message": "Authorization expired.",
                "type": "AuthorizationExpiredError",
                "code": 419,
            }
        },
        status_code=419,
        request_headers=TEST_HEADERS,
    )

    with pytest.raises(AuthorizationExpiredError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).create_table(
            identifier=("fokko", "fokko2"),
            schema=table_schema_simple,
            location=None,
            partition_spec=PartitionSpec(
                PartitionField(source_id=1, field_id=1000, transform=TruncateTransform(width=3), name="id")
            ),
            sort_order=SortOrder(SortField(source_id=2, transform=IdentityTransform())),
            properties={"owner": "fokko"},
        )
    assert "Authorization expired" in str(e.value)
    assert rest_mock.call_count == 3


def test_register_table_200(
    rest_mock: Mocker, table_schema_simple: Schema, example_table_metadata_no_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/default/register",
        json=example_table_metadata_no_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    actual = catalog.register_table(
        identifier=("default", "registered_table"), metadata_location="s3://warehouse/database/table/metadata.json"
    )
    expected = Table(
        identifier=("default", "registered_table"),
        metadata_location=example_table_metadata_no_snapshot_v1_rest_json["metadata-location"],
        metadata=TableMetadataV1(**example_table_metadata_no_snapshot_v1_rest_json["metadata"]),
        io=load_file_io(),
        catalog=catalog,
    )
    assert actual.metadata.model_dump() == expected.metadata.model_dump()
    assert actual.metadata_location == expected.metadata_location
    assert actual.name() == expected.name()


def test_register_table_409(rest_mock: Mocker, table_schema_simple: Schema) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/namespaces/default/register",
        json={
            "error": {
                "message": "Table already exists: fokko.fokko2 in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "AlreadyExistsException",
                "code": 409,
            }
        },
        status_code=409,
        request_headers=TEST_HEADERS,
    )

    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    with pytest.raises(TableAlreadyExistsError) as e:
        catalog.register_table(
            identifier=("default", "registered_table"), metadata_location="s3://warehouse/database/table/metadata.json"
        )
    assert "Table already exists" in str(e.value)


def test_delete_namespace_204(rest_mock: Mocker) -> None:
    namespace = "example"
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/{namespace}",
        json={},
        status_code=204,
        request_headers=TEST_HEADERS,
    )
    RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_namespace(namespace)


def test_delete_table_204(rest_mock: Mocker) -> None:
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/example/tables/fokko",
        json={},
        status_code=204,
        request_headers=TEST_HEADERS,
    )
    RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_table(("example", "fokko"))


def test_delete_table_from_self_identifier_204(
    rest_mock: Mocker, example_table_metadata_with_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/pdames/tables/table",
        json=example_table_metadata_with_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    table = catalog.load_table(("pdames", "table"))
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/pdames/tables/table",
        json={},
        status_code=204,
        request_headers=TEST_HEADERS,
    )
    catalog.drop_table(table.name())


def test_rename_table_200(rest_mock: Mocker, example_table_metadata_with_snapshot_v1_rest_json: Dict[str, Any]) -> None:
    rest_mock.post(
        f"{TEST_URI}v1/tables/rename",
        json={
            "source": {"namespace": ("pdames",), "name": "source"},
            "destination": {"namespace": ("pdames",), "name": "destination"},
        },
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/pdames/tables/destination",
        json=example_table_metadata_with_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    from_identifier = ("pdames", "source")
    to_identifier = ("pdames", "destination")
    actual = catalog.rename_table(from_identifier, to_identifier)
    expected = Table(
        identifier=("pdames", "destination"),
        metadata_location=example_table_metadata_with_snapshot_v1_rest_json["metadata-location"],
        metadata=TableMetadataV1(**example_table_metadata_with_snapshot_v1_rest_json["metadata"]),
        io=load_file_io(),
        catalog=catalog,
    )
    assert actual.metadata.model_dump() == expected.metadata.model_dump()
    assert actual == expected


def test_rename_table_from_self_identifier_200(
    rest_mock: Mocker, example_table_metadata_with_snapshot_v1_rest_json: Dict[str, Any]
) -> None:
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/pdames/tables/source",
        json=example_table_metadata_with_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN)
    from_identifier = ("pdames", "source")
    to_identifier = ("pdames", "destination")
    table = catalog.load_table(from_identifier)
    rest_mock.post(
        f"{TEST_URI}v1/tables/rename",
        json={
            "source": {"namespace": ("pdames",), "name": "source"},
            "destination": {"namespace": ("pdames",), "name": "destination"},
        },
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    rest_mock.get(
        f"{TEST_URI}v1/namespaces/pdames/tables/destination",
        json=example_table_metadata_with_snapshot_v1_rest_json,
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    actual = catalog.rename_table(table.name(), to_identifier)
    expected = Table(
        identifier=("pdames", "destination"),
        metadata_location=example_table_metadata_with_snapshot_v1_rest_json["metadata-location"],
        metadata=TableMetadataV1(**example_table_metadata_with_snapshot_v1_rest_json["metadata"]),
        io=load_file_io(),
        catalog=catalog,
    )
    assert actual.metadata.model_dump() == expected.metadata.model_dump()
    assert actual == expected


def test_delete_table_404(rest_mock: Mocker) -> None:
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/example/tables/fokko",
        json={
            "error": {
                "message": "Table does not exist: fokko.fokko2 in warehouse 8bcb0838-50fc-472d-9ddb-8feb89ef5f1e",
                "type": "NoSuchTableException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )
    with pytest.raises(NoSuchTableError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_table(("example", "fokko"))
    assert "Table does not exist" in str(e.value)


def test_create_table_missing_namespace(rest_mock: Mocker, table_schema_simple: Schema) -> None:
    table = "table"
    with pytest.raises(NoSuchIdentifierError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).create_table(table, table_schema_simple)
    assert f"Missing namespace or invalid identifier: {table}" in str(e.value)


def test_load_table_invalid_namespace(rest_mock: Mocker) -> None:
    table = "table"
    with pytest.raises(NoSuchIdentifierError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).load_table(table)
    assert f"Missing namespace or invalid identifier: {table}" in str(e.value)


def test_drop_table_invalid_namespace(rest_mock: Mocker) -> None:
    table = "table"
    with pytest.raises(NoSuchIdentifierError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_table(table)
    assert f"Missing namespace or invalid identifier: {table}" in str(e.value)


def test_purge_table_invalid_namespace(rest_mock: Mocker) -> None:
    table = "table"
    with pytest.raises(NoSuchIdentifierError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).purge_table(table)
    assert f"Missing namespace or invalid identifier: {table}" in str(e.value)


def test_create_namespace_invalid_namespace(rest_mock: Mocker) -> None:
    with pytest.raises(NoSuchNamespaceError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).create_namespace(())
    assert "Empty namespace identifier" in str(e.value)


def test_drop_namespace_invalid_namespace(rest_mock: Mocker) -> None:
    with pytest.raises(NoSuchNamespaceError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_namespace(())
    assert "Empty namespace identifier" in str(e.value)


def test_load_namespace_properties_invalid_namespace(rest_mock: Mocker) -> None:
    with pytest.raises(NoSuchNamespaceError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).load_namespace_properties(())
    assert "Empty namespace identifier" in str(e.value)


def test_update_namespace_properties_invalid_namespace(rest_mock: Mocker) -> None:
    with pytest.raises(NoSuchNamespaceError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).update_namespace_properties(())
    assert "Empty namespace identifier" in str(e.value)


def test_request_session_with_ssl_ca_bundle() -> None:
    # Given
    catalog_properties = {
        "uri": TEST_URI,
        "token": TEST_TOKEN,
        "ssl": {
            "cabundle": "path_to_ca_bundle",
        },
    }
    with pytest.raises(OSError) as e:
        # Missing namespace
        RestCatalog("rest", **catalog_properties)  # type: ignore
    assert "Could not find a suitable TLS CA certificate bundle, invalid path: path_to_ca_bundle" in str(e.value)


def test_request_session_with_ssl_client_cert() -> None:
    # Given
    catalog_properties = {
        "uri": TEST_URI,
        "token": TEST_TOKEN,
        "ssl": {
            "client": {
                "cert": "path_to_client_cert",
                "key": "path_to_client_key",
            }
        },
    }
    with pytest.raises(OSError) as e:
        # Missing namespace
        RestCatalog("rest", **catalog_properties)  # type: ignore
    assert "Could not find the TLS certificate file, invalid path: path_to_client_cert" in str(e.value)


def test_rest_catalog_with_basic_auth_type(rest_mock: Mocker) -> None:
    # Given
    rest_mock.get(
        f"{TEST_URI}v1/config",
        json={"defaults": {}, "overrides": {}},
        status_code=200,
    )
    # Given
    catalog_properties = {
        "uri": TEST_URI,
        "auth": {
            "type": "basic",
            "basic": {
                "username": "one",
                "password": "two",
            },
        },
    }
    catalog = RestCatalog("rest", **catalog_properties)  # type: ignore
    assert catalog.uri == TEST_URI

    encoded_user_pass = base64.b64encode(b"one:two").decode()
    expected_auth_header = f"Basic {encoded_user_pass}"
    assert rest_mock.last_request.headers["Authorization"] == expected_auth_header


def test_rest_catalog_with_custom_auth_type() -> None:
    # Given
    catalog_properties = {
        "uri": TEST_URI,
        "auth": {
            "type": "custom",
            "impl": "dummy.nonexistent.package",
            "custom": {
                "property1": "one",
                "property2": "two",
            },
        },
    }
    with pytest.raises(ValueError) as e:
        # Missing namespace
        RestCatalog("rest", **catalog_properties)  # type: ignore
    assert "Could not load AuthManager class for 'dummy.nonexistent.package'" in str(e.value)


def test_rest_catalog_with_custom_basic_auth_type(rest_mock: Mocker) -> None:
    # Given
    catalog_properties = {
        "uri": TEST_URI,
        "auth": {
            "type": "custom",
            "impl": "pyiceberg.catalog.rest.auth.BasicAuthManager",
            "custom": {
                "username": "one",
                "password": "two",
            },
        },
    }
    rest_mock.get(
        f"{TEST_URI}v1/config",
        json={"defaults": {}, "overrides": {}},
        status_code=200,
    )
    catalog = RestCatalog("rest", **catalog_properties)  # type: ignore
    assert catalog.uri == TEST_URI

    encoded_user_pass = base64.b64encode(b"one:two").decode()
    expected_auth_header = f"Basic {encoded_user_pass}"
    assert rest_mock.last_request.headers["Authorization"] == expected_auth_header


def test_rest_catalog_with_custom_auth_type_no_impl() -> None:
    # Given
    catalog_properties = {
        "uri": TEST_URI,
        "auth": {
            "type": "custom",
            "custom": {
                "property1": "one",
                "property2": "two",
            },
        },
    }
    with pytest.raises(ValueError) as e:
        # Missing namespace
        RestCatalog("rest", **catalog_properties)  # type: ignore
    assert "auth.impl must be specified when using custom auth.type" in str(e.value)


def test_rest_catalog_with_non_custom_auth_type_impl() -> None:
    # Given
    catalog_properties = {
        "uri": TEST_URI,
        "auth": {
            "type": "basic",
            "impl": "basic.package",
            "basic": {
                "username": "one",
                "password": "two",
            },
        },
    }
    with pytest.raises(ValueError) as e:
        # Missing namespace
        RestCatalog("rest", **catalog_properties)  # type: ignore
    assert "auth.impl can only be specified when using custom auth.type" in str(e.value)


def test_rest_catalog_with_unsupported_auth_type() -> None:
    # Given
    catalog_properties = {
        "uri": TEST_URI,
        "auth": {
            "type": "unsupported",
            "unsupported": {
                "property1": "one",
                "property2": "two",
            },
        },
    }
    with pytest.raises(ValueError) as e:
        # Missing namespace
        RestCatalog("rest", **catalog_properties)  # type: ignore
    assert "Could not load AuthManager class for 'unsupported'" in str(e.value)


EXAMPLE_ENV = {"PYICEBERG_CATALOG__PRODUCTION__URI": TEST_URI}


@mock.patch.dict(os.environ, EXAMPLE_ENV)
@mock.patch("pyiceberg.catalog.Config.get_catalog_config")
def test_catalog_from_environment_variables(catalog_config_mock: mock.Mock, rest_mock: Mocker) -> None:
    env_config: RecursiveDict = Config._from_environment_variables({})
    catalog_config_mock.return_value = cast(RecursiveDict, env_config.get("catalog")).get("production")
    catalog = cast(RestCatalog, load_catalog("production"))
    assert catalog.uri == TEST_URI


@mock.patch.dict(os.environ, EXAMPLE_ENV)
@mock.patch("pyiceberg.catalog._ENV_CONFIG.get_catalog_config")
def test_catalog_from_environment_variables_override(catalog_config_mock: mock.Mock, rest_mock: Mocker) -> None:
    rest_mock.get(
        "https://other-service.io/api/v1/config",
        json={"defaults": {}, "overrides": {}},
        status_code=200,
    )
    env_config: RecursiveDict = Config._from_environment_variables({})

    catalog_config_mock.return_value = cast(RecursiveDict, env_config.get("catalog")).get("production")
    catalog = cast(RestCatalog, load_catalog("production", uri="https://other-service.io/api"))
    assert catalog.uri == "https://other-service.io/api"


def test_catalog_from_parameters_empty_env(rest_mock: Mocker) -> None:
    rest_mock.get(
        "https://other-service.io/api/v1/config",
        json={"defaults": {}, "overrides": {}},
        status_code=200,
    )

    catalog = cast(RestCatalog, load_catalog("production", uri="https://other-service.io/api"))
    assert catalog.uri == "https://other-service.io/api"


def test_table_identifier_in_commit_table_request(
    rest_mock: Mocker, table_schema_simple: Schema, example_table_metadata_v2: Dict[str, Any]
) -> None:
    metadata_location = "s3://some_bucket/metadata.json"
    rest_mock.post(
        url=f"{TEST_URI}v1/namespaces/namespace/tables/table_name",
        json={
            "metadata": example_table_metadata_v2,
            "metadata-location": metadata_location,
        },
        status_code=200,
        request_headers=TEST_HEADERS,
    )
    catalog = RestCatalog("catalog_name", uri=TEST_URI, token=TEST_TOKEN)
    table = Table(
        identifier=("namespace", "table_name"),
        metadata=None,  # type: ignore
        metadata_location=metadata_location,
        io=None,  # type: ignore
        catalog=catalog,
    )
    catalog.commit_table(table, (), ())
    assert (
        rest_mock.last_request.text
        == """{"identifier":{"namespace":["namespace"],"name":"table_name"},"requirements":[],"updates":[]}"""
    )


def test_drop_view_invalid_namespace(rest_mock: Mocker) -> None:
    view = "view"
    with pytest.raises(NoSuchIdentifierError) as e:
        # Missing namespace
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_view(view)

    assert f"Missing namespace or invalid identifier: {view}" in str(e.value)


def test_drop_view_404(rest_mock: Mocker) -> None:
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/some_namespace/views/does_not_exists",
        json={
            "error": {
                "message": "The given view does not exist",
                "type": "NoSuchViewException",
                "code": 404,
            }
        },
        status_code=404,
        request_headers=TEST_HEADERS,
    )

    with pytest.raises(NoSuchViewError) as e:
        RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_view(("some_namespace", "does_not_exists"))
    assert "The given view does not exist" in str(e.value)


def test_drop_view_204(rest_mock: Mocker) -> None:
    rest_mock.delete(
        f"{TEST_URI}v1/namespaces/some_namespace/views/some_view",
        json={},
        status_code=204,
        request_headers=TEST_HEADERS,
    )
    RestCatalog("rest", uri=TEST_URI, token=TEST_TOKEN).drop_view(("some_namespace", "some_view"))
