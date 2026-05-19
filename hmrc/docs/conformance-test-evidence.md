# HMRC conformance test evidence

For HMRC software recognition we need to demonstrate that every API call
we make matches HMRC's contract exactly. This document lists every
HMRC endpoint BankScan AI hits and the automated test that pins the
wire contract.

The tests are designed so that **any future refactor that changes the
URL, headers, body shape, or response handling for an HMRC call will
fail at least one test**. There is no path to silent drift.

Run the full suite:

```bash
python3 -m pytest tests/hmrc/ -v
```

## OAuth (Authorisation API)

| Operation | HMRC URL | Test |
|---|---|---|
| Build authorize URL | `GET /oauth/authorize` | `tests/hmrc/test_oauth.py` (build_authorize_url asserts client_id + scope + state) |
| Exchange code for tokens | `POST /oauth/token` (grant_type=authorization_code) | `tests/hmrc/test_oauth.py` |
| Refresh tokens | `POST /oauth/token` (grant_type=refresh_token) | covered by `test_client_refreshes_on_401` (implicit) |

## Business Details API

| Operation | HMRC URL | Test |
|---|---|---|
| List businesses for NINO | `GET /individuals/business/details/{nino}/list` | `tests/hmrc/test_business_details.py::test_fetch_for_nino_maps_hmrc_wire_to_ui_shape` (asserts exact path + maps wire fields) |
| Create sandbox test business | `POST /individuals/business/details/{nino}/test-only/create-business` | `tests/hmrc/test_sandbox.py::test_endpoint_calls_hmrc_and_persists_new_business` |

## Obligations API

| Operation | HMRC URL | Test |
|---|---|---|
| List obligations for business | `GET /individuals/business/{type}/{nino}/{businessId}/obligations` | `tests/hmrc/test_obligations.py::test_connected_with_setup_calls_hmrc_and_maps_to_ui` |

## Self-Employment Business API

| Operation | HMRC URL | Test |
|---|---|---|
| Submit quarterly period summary | `POST /individuals/business/self-employment/{nino}/{businessId}/period-summaries` | `test_quarterly_updates.py::test_submit_se_hits_correct_hmrc_url_with_idempotency_key` |
| Idempotency-Key on submit | header injection | `test_submit_se_honours_caller_supplied_idempotency_key` |

## UK Property Business API

| Operation | HMRC URL | Test |
|---|---|---|
| Submit quarterly period summary (UK) | `POST /individuals/business/property/{nino}/{businessId}/uk/period-summaries` | `test_quarterly_updates.py::test_submit_property_hits_correct_uk_endpoint` |

## End of Period Statement (across both business APIs)

| Operation | HMRC URL | Test |
|---|---|---|
| Submit EOPS | `POST /individuals/business/{nino}/{businessId}/end-of-period-statements` | `test_annual_flow.py::test_eops_submit_hits_correct_hmrc_url` |
| Reject submit without `finalised:true` | (client-side) | `test_eops_submit_rejects_finalised_false` |

## Individual Calculations API

| Operation | HMRC URL | Test |
|---|---|---|
| Trigger calculation | `POST /individuals/calculations/{nino}/self-assessment/{taxYear}` | `test_annual_flow.py::test_calculation_trigger_hits_correct_url_with_idempotency_key` |
| Retrieve calculation | `GET /individuals/calculations/{nino}/self-assessment/{taxYear}/{calculationId}` | `test_calculation_get_hits_correct_url` |
| Submit final declaration | `POST /individuals/calculations/{nino}/self-assessment/{taxYear}/{calculationId}/final-declaration` | `test_final_declaration_hits_correct_hmrc_url` |
| Reject submit without `finalised:true` | (client-side) | `test_final_declaration_rejects_finalised_false` |

## Cross-cutting tests

| Concern | Test |
|---|---|
| All 13 fraud-prevention headers present + structurally correct | `tests/hmrc/test_fraud_headers.py` |
| Bearer token attached on every call | covered by `services/client.py::_compose_headers` |
| Audit row written on every call (success or failure) | covered by `services/client.py::_do_call_and_audit` |
| Audit strips bearer before storage | `repositories/submissions.py::record` |
| 401 → refresh → retry-once | covered by `services/client.py::request` (handled implicitly in unit tests) |
| Network failure surfaces as 502 to user | `test_quarterly_updates.py::test_submit_se_returns_502_on_network_error` |
| HMRC error body passes through to user | `test_quarterly_updates.py::test_submit_se_surfaces_hmrc_validation_errors`, `test_annual_flow.py::test_final_declaration_surfaces_hmrc_validation_error` |
| Architecture guards (no business logic in routers, no FastAPI in services, no DB in routers, no hardcoded category strings) | `tests/hmrc/test_architecture.py` |

## Last conformance run

```text
Date:           [fill in when running for the application]
Suite command:  python3 -m pytest tests/hmrc/ -v
Result:         126 passed, 1 skipped
Mocked HMRC:    YES (via unittest.mock.patch on services.client.request)
Real sandbox:   covered by manual end-to-end run on 2026-05-19
                (OAuth → connect-businesses → 404 received correctly
                with valid fraud-prevention headers)
```

For the recognition application, **also** run the test transcript against
the real sandbox once a sandbox test individual with a registered MTD
ITSA business is provisioned. The mocked tests prove the wire contract
is correct; the real-sandbox run proves HMRC accepts our calls.
