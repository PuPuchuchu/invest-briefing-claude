# Step 10J-QA — FRED Provider Live Integration QA

날짜: 2026-10-05
상태: QA 완료. **GitHub 커밋/푸시 미실행 (별도 승인 대기, sandbox git commit/push 영구 금지)**

---

## A. Scope

목표는 "오프라인 테스트가 통과했다"가 아니라, production FRED provider(`src/macro/fred_provider.py::FredMacroProvider`)가 구조적으로 타당하고, 실제 crosstab 응답을 내부 PIT schema로 정확히 변환하는지를 재검증하는 것. 이번 라운드에서 다음을 수행:

1. 코드 재독 (아래 "B" 목록 전체 — Step 10J 당시 확정된 frozen contract 재확인)
2. `fred_provider.py` 869줄 구조 감사 (9개 항목)
3. 내부 데이터 계약(schema.py) 대조 검증
4. `vintage_dates` 배치 정책 재검토
5. 실제 FRED API에 연결하는 **신규** `workflow_dispatch` 전용 live smoke test 추가 (Step 10I 검증기와 별개, production code path 자체를 호출)
6. 보안 재감사
7. 매크로/전체 리포지토리 회귀 테스트, baseline 대비 비교

---

## B. Current implementation (재확인한 frozen contract)

다음 파일을 이번 라운드에서 **실제 현재 내용 기준으로 다시 읽음**:
`src/macro/fred_provider.py`(전체 869→898줄), `tests/test_macro_fred_provider.py`, `src/macro/pit.py`, `src/macro/schema.py`, `src/macro/provider.py`, `claude/2026-10-04-step10j-fred-provider-pit-production-integration.md`.

확인된 사실 (Step 10I 실측과 일치):
- `output_type=2`는 PIT-shaped row가 아니라 crosstab 구조 — 관측일은 `"date"`, vintage는 `"<SERIES_ID>_<YYYYMMDD>"` 동적 컬럼.
- `fred_provider.py`는 이 구조를 `_parse_crosstab_response()`/`_extract_vintage_cells()`로 내부 long-form row로 flatten함 — Step 10I 검증 스크립트에서 포팅된 동일 로직.
- `"."`는 NaN으로 처리 (0/None/drop 금지) — 확인됨.
- `fred/series` metadata endpoint에서 frequency/units/seasonal adjustment를 가져옴 — 확인됨.
- `src/macro/pit.py::select_latest_vintage_as_of`는 **이번 라운드에서도 수정하지 않음**. provider와의 연결 문제가 실제로 발견되지 않았으므로 PIT 알고리즘은 변경 대상이 아니었음 (아래 "C"에서 발견된 유일한 문제는 PIT 쪽이 아니라 HTTP 에러 진단 쪽).

---

## C. Structural audit (9개 항목)

869줄 전체를 라인 단위로 재독하고, 추가로 `pyflakes`로 미사용 변수/임포트를 기계적으로 스캔했다.

| # | 질문 | 판정 |
|---|---|---|
| 1 | `provider.py`와 중복 책임? | 없음 — `provider.py`는 ABC + `InMemoryMacroProvider`만 정의, `fred_provider.py`는 그 인터페이스를 구현만 함. |
| 2 | HTTP request/retry/error handling 과도 중복? | 아니오 — 재시도(retry) 로직 자체가 없고, 에러 분류만 한 번씩 수행. 과도하지 않음. |
| 3 | crosstab flattening logic 분리? | 명확히 분리됨 ("LAYER 2" 섹션: `_vintage_column_pattern`/`_extract_vintage_cells`/`_parse_crosstab_response`). |
| 4 | metadata 처리와 observations 처리 결합? | `get_series()`가 오케스트레이션 레벨에서 둘을 조합하지만, 각각 독립 메서드(`get_series_metadata()` vs `_fetch_wide_window()`/`_fetch_explicit_vintage_dates()`)로 분리되어 있어 불필요한 결합은 아님. |
| 5 | PIT adapter와 FRED transport 강결합? | 없음 — 이 파일은 `pit.py`를 import조차 하지 않음. `get_series()`는 PIT-비인지(not PIT-aware)로 전체 row를 반환할 뿐. |
| 6 | 사용되지 않는 abstraction/helper/class? | **1건 발견 (실제 버그) — 아래 "J" 참조.** `_FredHttpClient.request_json()`이 `redacted = _redact_url(url)`을 계산하지만 어떤 예외에도 전달하지 않아 `pyflakes`가 "assigned but never used"로 플래그. 나머지는 전부 사용됨. |
| 7 | API 호출 정책과 데이터 변환 정책이 한 파일에 과몰입? | 한 파일(869줄)에 HTTP/크로스탭파싱/raw저장/Provider 오케스트레이션이 공존하지만, 파일 내부에 "LAYER 1/2/3" 주석으로 명확히 구획되어 있고, `src/sec/fetcher.py`가 이미 같은 패턴(단일 도메인 provider 파일에 transport+변환+저장을 모두 포함)을 쓰고 있어 이 코드베이스의 기존 컨벤션과 일치. 구조적 결함으로 판단하지 않음. |
| 8 | 향후 다른 macro provider 추가 시 generic interface 오염 가능성? | 없음 — `WIDE_WINDOW_SERIES_IDS` 등 FRED 전용 상수는 전부 `fred_provider.py` 내부에만 존재. `provider.py`의 `MacroDataProvider` ABC는 무변경. |
| 9 | 869줄이 기능상 필요한가, 방어적 중복인가? | 모듈 docstring(~140줄, 설계사 재구성 근거 포함)·에러 클래스(~55줄)·HTTP client(~70줄)·크로스탭 파서(~140줄)·raw저장(~40줄)·Provider 오케스트레이션(~290줄)로 구성. 중복 코드나 불필요한 추상화는 (6번 항목의 단일 버그 제외) 발견되지 않음. **줄 수를 줄이기 위한 리팩터링은 수행하지 않음** (요청사항 준수). |

**결론**: 구조적으로 타당함. 유일하게 발견된 결함(#6)은 라인 수 문제가 아니라 실제 진단 가능성(diagnosability) 결함이었고, 최소 수정으로 해결함 (아래 "J"/"K").

---

## D. Data contract audit

`schema.py`의 `RAW_OBSERVATION_REQUIRED_FIELDS`/`VALID_FREQUENCIES`와 `fred_provider.py`의 출력 dict를 필드 단위로 대조:

| 필드 | 검증 결과 |
|---|---|
| `series_id` | 항상 호출자가 넘긴 문자열 그대로, 변형 없음. |
| `observation_date` | crosstab의 `"date"` 키 값 그대로 (ISO 문자열), 재가공 없음. |
| `vintage_date` | 동적 컬럼명 `"{series}_{YYYYMMDD}"`에서 `datetime.strptime(..., "%Y%m%d")`로 엄격 파싱 — 파싱 실패 시 `FredMalformedVintageColumnError`로 **중단** (Fixture D로 확인), 정상값처럼 허용하지 않음. |
| `value` | `_parse_numeric_cell()` — 정규식 `^-?\d+(\.\d+)?$`에 매치하지 않으면 전부 `float("nan")`. 반올림(rounding) 없음 — `float(text)`로 문자열을 그대로 변환. |
| `frequency` | FRED 원문(`"Monthly"` 등)을 `_normalize_frequency()`로 `schema.VALID_FREQUENCIES` 어휘로 변환, 미인식 값은 `None` (추측 없음 → `validate_raw_observation()`에서 required-field 실패로 fail-closed). |
| `units` | metadata의 `units_short` 우선, 없으면 `units` — 가공 없음. |
| missing/NaN | `"."` → NaN, 절대 0.0 아님, row 삭제 안 함 (Fixture C). |
| `source` | 항상 리터럴 `"FRED"` — 응답에서 유도하지 않음. |
| metadata | 매번 `fred/series` 호출로 fetch, 하드코딩 없음. |

**고유성(identity) 검증**: `(series_id, observation_date, vintage_date)`가 하나의 고유 observation identity여야 한다는 요구사항에 대해 — `fred_provider.py`는 모든 row에서 `realtime_start = vintage_date`로 설정하므로 (모듈 docstring에 명시된 의도적 설계), `schema.validate_raw_series()`의 실제 중복 판정 키 `(series_id, observation_date, realtime_start, vintage_date)`는 FRED provider 출력에 대해서는 사실상 `(series_id, observation_date, vintage_date)`와 동치가 된다. 이를 실제 테스트로 재확인:

```python
>>> from src.macro.schema import validate_raw_series
>>> rows = [{"series_id": "CPIAUCSL", "observation_date": "2015-01-01",
...          "value": 1.0, "realtime_start": "2015-02-26", "source": "FRED",
...          "frequency": "MONTHLY", "units": "U", "vintage_date": "2015-02-26"}] * 2
>>> validate_raw_series(rows)
['rows[1].duplicate_of_rows[0]']
```
(실제 실행하여 확인 — 동일 `(series_id, observation_date, vintage_date)` row 2개를 넣으면 2번째가 정확히 중복으로 플래그됨. deterministic.)

**정렬(sort order) 검증**: `fred_provider.get_series()` 자체는 FRED 응답이 들어온 순서를 보존할 뿐 재정렬하지 않음(Python 3.7+ dict는 삽입 순서를 보존하므로 같은 입력 → 같은 출력, deterministic). 최종 사용자가 보는 시계열은 `pit.py::get_point_in_time_series()`가 `observation_date` 기준 오름차순으로 정렬하여 반환 — 이 부분도 무변경.

**미래 vintage가 PIT에 유입될 가능성**: `pit.py`가 무변경이므로 기존 Step 10J의 no-look-ahead 회귀 테스트가 여전히 PASS (아래 "G").

**결론**: 데이터 계약 위반 없음.

---

## E. Vintage handling audit

- `VINTAGE_DATES_BATCH_SIZE = 25` — 이미 모듈 docstring에서 "conservative, DOCUMENTED placeholder... NOT empirically confirmed against FRED's actual server-side limit"로 명시되어 있었고, 생성자 파라미터(`vintage_dates_batch_size`)로 이미 configuration 가능하게 노출되어 있었음 — 이 요구사항은 Step 10J 당시 이미 충족된 상태였음을 재확인.
- 다만 이 숫자가 "검증된 사실"처럼 보일 위험을 낮추기 위해, 25라는 값 자체가 **UNVERIFIED / NOT EMPIRICALLY ESTABLISHED**임을 본 QA 문서에서도 명시적으로 재확인 (아래 "J" open items). 추가 코드 변경은 하지 않음 — 이미 configuration parameter로 노출되어 있고, "엔지니어링 기본값"이라는 문서화도 이미 존재하므로 중복 수정하지 않음.
- 배치가 데이터를 누락시키지 않는지: `_batch()`는 리스트를 비중복 슬라이스로 분할하고, `_fetch_explicit_vintage_dates()`는 모든 배치를 순회하며 `all_records.extend(...)` — window 내 모든 vintage date가 결국 fetch됨. 코드 검토로 확인.

---

## F. Live FRED validation

**신규 작성**: `.github/scripts/fred_provider_live_smoke_test.py` (306줄) + `.github/workflows/fred-provider-live-smoke-test.yml` (`workflow_dispatch`-only, 스케줄 없음).

이 스크립트는 Step 10I 검증기와 달리 **자체 파서를 재구현하지 않고**, `src/macro/fred_provider.py::FredMacroProvider`를 직접 import하여 호출한다 — production code path 자체를 실제 FRED API로 검증하는 것이 목적.

검증 섹션(A~G, 요구사항과 1:1 대응):
- A. connectivity / 4개 시리즈(CPIAUCSL/PAYEMS/T10Y2Y/VIXCLS) 분류 커버리지 확인
- B. `get_series_metadata()` 호출 — frequency/units/seasonal_adjustment
- C/D. `get_series()` 호출 — crosstab 응답이 실제로 normalized rows로 flatten되는지, 모든 row가 `schema.validate_raw_observation()`을 통과하는지, `.` → NaN 처리가 실제로 발생했는지
- E. 하나의 observation_date에 복수 vintage가 존재하는지
- F. PAYEMS에서 동일 observation_date에 서로 다른 값을 가진 vintage가 실제로 존재하는지
- G. 실제 provider 출력을 **변경하지 않은** `select_latest_vintage_as_of()`/`get_point_in_time_series()`에 통과시켜 PIT 동작(첫 vintage 이전=None, 이후=값 선택, 미래 vintage 비노출) 확인

**실행 상태 — UNVERIFIED / NOT EMPIRICALLY ESTABLISHED (이번 라운드에서 실제 live 실행 안 됨)**: 이 sandbox에는 `FRED_API_KEY`가 없고, 이 워크플로우는 `workflow_dispatch`로 사용자가 GitHub Actions에서 직접 트리거해야 하는 구조(Step 10G/10I와 동일한 패턴)다. 따라서 이번 라운드에서는:
1. 스크립트 문법 검증(`ast.parse`) — PASS.
2. **오프라인 mock 기반 dry-run** — `urllib.request.urlopen`을 Fixture B 스타일(PAYEMS 실제 수정치 패턴 포함) 데이터로 monkeypatch하여 A~G 섹션 전체가 에러 없이 실행되고, 에러 경로(HTTPError 발생 시 `redacted_url`에 키가 노출되지 않음)도 확인 — **PASS** (exit code 0, 그리고 에러 케이스 exit code 1, 세부 출력은 아래 "I" 참조).
3. **실제 `api.stlouisfed.org`에 대한 live 호출은 수행하지 않았다.** 이는 이번 QA 문서가 "검증되지 않은 부분은 UNVERIFIED로 표시하라"는 지시에 따라 명시적으로 플래그하는 항목이며, 실제 GitHub Actions 환경에서 `secrets.FRED_API_KEY`로 이 워크플로우를 수동 트리거해야 최종 확인된다.

---

## G. PIT validation

`src/macro/pit.py`는 이번 라운드에서 **단 한 줄도 수정하지 않음**. 기존 Step 10J의 PIT 통합 테스트(Case 1~5 + no-look-ahead 회귀 2건)가 전부 그대로 통과함(아래 "I"). 신규 live smoke test의 Section G도 동일한 frozen 함수(`select_latest_vintage_as_of`, `get_point_in_time_series`)를 import하여 호출하도록 작성 — 재구현 없음.

---

## H. Security validation

| 점검 항목 | 결과 |
|---|---|
| source code 내 하드코딩된 FRED key | 없음 (`src/macro/fred_provider.py`, `tests/test_macro_fred_provider.py`, `.github/scripts/fred_provider_live_smoke_test.py`, `.github/workflows/fred-provider-live-smoke-test.yml` 전체 정규식 스캔 — 0건) |
| test fixture 내 실제 key | 없음 — 전부 `"test-key-not-real"` 또는 의도적 노출-테스트용 `"super-secret-value-12345"` |
| workflow log에 secret 노출 | 워크플로우는 `secrets.FRED_API_KEY`를 env var로만 전달, echo/print 없음 |
| exception string에 API key 포함 가능성 | **이번 라운드에서 발견/수정** — 아래 "J"/"K" 참조. 수정 후 재테스트로 "redacted_url에 키가 없음"을 명시적으로 검증 |
| URL logging 전 redaction | `_redact_url()`이 모든 raise 지점에 일관되게 적용되도록 이번 라운드에서 수정 (`redacted_url=redacted` 전달) |
| `.env`/secret material을 repo에 추가 | 추가하지 않음. `.gitignore`에 `.env` 항목이 없는 pre-existing 이슈는 그대로 열려 있음(이번 라운드 범위 아님, Step 10H부터 이어진 open item) |

---

## I. Regression test comparison

| | baseline (이번 QA 시작 전) | QA 이후 |
|---|---|---|
| macro suite (`tests/test_macro_*.py`) | 137 passed | **138 passed** (+1, 신규 redacted_url 회귀 테스트) |
| 전체 repo: passed | 1081 | **1082 (+1)** |
| 전체 repo: failed | 12 | **12 (동일, 전부 기존 SEC companyfacts 캐시 누락)** |
| 전체 repo: skipped | 23 | **23 (동일)** |

새로운 failure 0건. 기존 12건은 `tests/test_stockholders_equity_coverage.py`의 로컬 SEC raw 캐시 파일 부재(MSFT/AAPL/NVDA/AVGO/INTC/MU/AMD/PLTR/ORCL/SMCI/CRWV + 요약 테스트 1건)로, 이번 FRED provider 작업과 무관함 — 테스트 이름까지 정확히 동일하게 재확인.

`tests/test_macro_fred_provider.py` 단독: **34 passed** (기존 33 + 신규 1).

---

## J. Problems found

1. **(실제 코드 버그, 수정됨)** `_FredHttpClient.request_json()`이 `redacted = _redact_url(url)`을 계산했지만 어떤 `raise FredHTTPError(...)` 호출에도 전달하지 않았다 (`pyflakes`로 확인: "local variable 'redacted' is assigned to but never used"). 결과적으로 어떤 FRED HTTP 실패가 발생해도 예외 메시지에 요청 URL/엔드포인트 정보가 전혀 없어, 실패를 특정 series_id/엔드포인트로 추적할 수 없었다 — 이번 QA가 요구하는 "sanitized diagnostic" 요구사항(섹션 6)과 직접 충돌하는 결함.
2. `VINTAGE_DATES_BATCH_SIZE=25`는 여전히 UNVERIFIED — 실제 FRED 서버의 `vintage_dates` 파라미터 당도 한도는 Step 10F/10G/10I/10J/이번 QA 어느 라운드에서도 실측되지 않았다. (이미 알려진 open item, 재확인만 함)
3. 신규 live smoke test workflow는 아직 실제 GitHub Actions 환경에서 한 번도 실행되지 않았다 (sandbox에 `FRED_API_KEY` 없음) — mock 기반 dry-run으로만 검증됨.

구조적 결함, PIT look-ahead 가능성, 또는 data contract 불일치는 **발견되지 않음**.

---

## K. Changes made

| 파일 | 변경 내용 |
|---|---|
| `src/macro/fred_provider.py` | `FredHTTPError.__init__`에 `redacted_url` 파라미터/속성 추가, 모든 `raise FredHTTPError(...)` 호출 지점에서 `redacted_url=redacted` 전달 (위 J-1 수정). 해당 docstring 갱신. **그 외 어떤 로직도 변경하지 않음** — 줄 수 축소를 위한 리팩터링 없음. |
| `tests/test_macro_fred_provider.py` | 기존 `test_api_key_never_appears_in_raised_exception_message`에 `redacted_url`도 키를 포함하지 않는지 확인하는 assertion 추가. 신규 `test_http_error_carries_redacted_url_for_diagnosis` 추가 (J-1 수정의 회귀 테스트). |
| `.github/scripts/fred_provider_live_smoke_test.py` (신규) | production `FredMacroProvider`를 실제 FRED API에 연결하는 `workflow_dispatch` 전용 smoke test. Step 10I 검증기와 중복되지 않음(자체 파서 재구현 없음). |
| `.github/workflows/fred-provider-live-smoke-test.yml` (신규) | 위 스크립트를 실행하는 workflow_dispatch 전용 워크플로우. 기존 Step 10I/10G 워크플로우는 수정하지 않음. |

**변경하지 않은 파일**: `src/macro/pit.py`, `schema.py`, `transforms.py`, `risk_state.py`, `growth_state.py`, `inflation_state.py`, `policy_state.py`, `curve_state.py`, `regime.py`, `engine.py`, `provider.py`, 기존 `.github/workflows/fred-connectivity-smoke-test.yml`, `.github/workflows/fred-multi-vintage-validation.yml`, `.github/scripts/fred_multi_vintage_validation.py`, `.github/scripts/fred_connectivity_smoke_test.py` — 전부 이번 라운드에서 손대지 않음.

이번 QA에서 하지 않은 것(요구사항 10번 준수): `CURVE_WINDOW` 확정 안 함, `regime_confidence` 추가 안 함, Composite Score 추가 안 함, macro state logic 변경 안 함, SEC 코드 수정 안 함, 임의 threshold 추가 안 함, historical backfill 정책 확정 안 함, API 제한을 추측해서 사실처럼 문서화 안 함, sandbox git commit/push 안 함.

---

## L. Final verdict

### **CONDITIONAL_PASS**

판정 근거:
- 핵심 기능(구조, 데이터 계약, PIT 연동, 보안, 회귀)은 전부 정상이며, 이번 라운드에서 발견된 유일한 실제 결함(J-1, 진단 정보 누락)은 최소 수정으로 해결하고 회귀 테스트로 고정했다.
- 그러나 다음 두 가지는 "명확한 engineering follow-up이 남아 있음"에 정확히 해당한다:
  1. `VINTAGE_DATES_BATCH_SIZE=25`가 여전히 **empirical confirmation 전** (사용자가 제시한 CONDITIONAL_PASS의 예시 사유와 정확히 일치).
  2. 신규 live smoke test가 **실제 FRED API에 대해 한 번도 실행되지 않음** — mock dry-run으로만 production code path를 검증했을 뿐, 실제 네트워크 상에서의 최종 확인은 사용자가 GitHub Actions에서 `fred-provider-live-smoke-test.yml`을 수동 트리거해야 완료된다.

FAIL에 해당하는 조건(실제 API provider path 실패, crosstab normalization 오류, PIT look-ahead 가능성, secret exposure, 기존 동작 regression, data contract 불일치) 중 **어느 것도 확인되지 않았음**.

---

## 최종 보고 요약 (사용자에게 제공할 10개 항목)

1. **변경된 파일**: `src/macro/fred_provider.py`(수정), `tests/test_macro_fred_provider.py`(수정, +1 테스트), `.github/scripts/fred_provider_live_smoke_test.py`(신규), `.github/workflows/fred-provider-live-smoke-test.yml`(신규).
2. **변경 이유**: 구조 감사에서 발견된 실제 버그(HTTP 에러 발생 시 redacted URL이 예외에 전달되지 않아 진단 불가) 수정 + 요구된 live FRED smoke test 추가.
3. **구조 감사 결과**: 9개 항목 중 8개 클린, 1개(미사용 `redacted` 변수) 발견 후 수정.
4. **실제 FRED API 테스트 결과**: 스크립트 작성 및 offline mock dry-run으로 A~G 전 섹션 PASS 확인. **실제 live API 호출은 미실행** (UNVERIFIED).
5. **PIT 결과**: `pit.py` 무변경, 기존 5개 PIT 통합 테스트 + 2개 no-look-ahead 회귀 테스트 전부 PASS.
6. **테스트 결과**: `test_macro_fred_provider.py` 34 passed(기존 33+신규 1), macro suite 138 passed, 전체 repo 1082 passed/12 failed/23 skipped.
7. **baseline 대비 diff**: failed/skipped 동일(12/23), passed +1(신규 회귀 테스트), 새 실패 0건.
8. **unresolved items**: (a) `VINTAGE_DATES_BATCH_SIZE=25` 미확정, (b) live smoke test 워크플로우 실제 미실행, (c) 기존부터 열려있던 `.gitignore`/`.env` 이슈.
9. **최종 verdict**: **CONDITIONAL_PASS**.
10. **GitHub commit 가능 여부**: 코드/테스트/워크플로우 전부 커밋 준비는 되었으나, **아직 커밋/푸시하지 않았음**. Sandbox에서의 git commit/push는 영구 금지이며, 커밋은 사용자의 별도 명시적 승인 후 기존 GitHub 웹 UI 방식(ClipboardEvent paste + SHA-256 검증)으로만 진행한다.
