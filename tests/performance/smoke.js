/**
 * Reliability load gate — the Phase 7 smoke profile (ADR 0025).
 *
 * 60 seconds of sustained 25 check-ins/s against the local API, each check-in
 * paired with its check-out, plus manager board reads. The profile spreads
 * work over 50 deterministic synthetic accounts (`reset_load_fixtures.py`):
 * mutation budgets are per session user, so 50 users give 6,000 requests per
 * minute against the 3,000 the profile spends — the spec's ≥2× headroom.
 *
 * Sessions are pre-provisioned by the fixture (raw tokens in a 0600 temp file
 * via `LOAD_SESSIONS_FILE`), so the load profile never logs in and never sends
 * `X-Forwarded-For`. The auth tier has its own integration tests.
 *
 * Thresholds are code, not opinions: p95 < 300 ms on every scenario request
 * and zero errors (no 5xx, no 429/503 — `http_req_failed` counts any non-2xx).
 * A threshold breach fails the command.
 */

import { check, fail } from "k6";
import exec from "k6/execution";
import http from "k6/http";

const BASE_URL = __ENV.LOAD_BASE_URL || "http://127.0.0.1:8010";
const USER_COUNT = Number(__ENV.LOAD_USER_COUNT || 50);
const CHECK_IN_RATE = Number(__ENV.LOAD_CHECK_IN_RATE || 25);
const DURATION = __ENV.LOAD_DURATION || "60s";
// One board read per second at the default 25 check-ins/s; scale with the rate.
const BOARD_READ_EVERY = Math.max(1, Math.round(CHECK_IN_RATE));

const JSON_HEADERS = { "Content-Type": "application/json" };
const CHECK_IN_EXPECTED = http.expectedStatuses(201, 409);

const SESSION_FILE = __ENV.LOAD_SESSIONS_FILE;
if (!SESSION_FILE) {
  throw new Error("LOAD_SESSIONS_FILE is required; run the gate through `make check-load`.");
}
const FIXTURE_SESSIONS = JSON.parse(open(SESSION_FILE)).sessions;

export const options = {
  scenarios: {
    smoke: {
      executor: "constant-arrival-rate",
      rate: CHECK_IN_RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: USER_COUNT,
      maxVUs: USER_COUNT,
      tags: { scenario: "smoke" },
    },
  },
  thresholds: {
    checks: ["rate==1"],
    "http_req_failed{scenario:smoke}": ["rate==0"],
    // p95 API latency across check-ins, check-outs and board reads.
    "http_req_duration{scenario:smoke}": ["p(95)<300"],
    dropped_iterations: ["count==0"],
  },
};

export function setup() {
  if (!Array.isArray(FIXTURE_SESSIONS) || FIXTURE_SESSIONS.length !== USER_COUNT) {
    fail(
      `LOAD_SESSIONS_FILE has ${FIXTURE_SESSIONS?.length ?? 0} sessions, ` +
        `expected ${USER_COUNT}; re-run the fixture provisioning.`,
    );
  }
  const sessions = FIXTURE_SESSIONS.map(({ email, token }) => ({
    email,
    cookie: token,
  }));
  // index 0 is the manager fixture (`load-01`); board reads need that role.
  return { sessions, manager: sessions[0] };
}

function authHeaders(session) {
  return {
    ...JSON_HEADERS,
    Cookie: `wp_session=${session.cookie}`,
  };
}

function closeOpenRecords(session) {
  const headers = authHeaders(session);
  const list = http.get(`${BASE_URL}/api/v1/attendance/records?page_size=10`, {
    headers,
    tags: { request: "recovery_list" },
  });
  if (list.status !== 200) return;
  const items = list.json().items || [];
  for (const item of items) {
    if (item.checked_out_at === null) {
      http.post(
        `${BASE_URL}/api/v1/attendance/records/${item.id}/check-out`,
        "{}",
        { headers, tags: { request: "recovery_check_out" } },
      );
    }
  }
}

function checkIn(session) {
  const headers = authHeaders(session);
  let response = http.post(
    `${BASE_URL}/api/v1/attendance/check-ins`,
    JSON.stringify({ work_mode: "onsite" }),
    {
      headers,
      tags: { request: "check_in" },
      // A 409 is the recovery path, not a load error; the check below still
      // requires the final response to be 201.
      responseCallback: CHECK_IN_EXPECTED,
    },
  );

  if (response.status === 409) {
    // An earlier interrupted iteration left an open record; recover and retry
    // once so a transient failure cannot poison the rest of that user's run.
    closeOpenRecords(session);
    response = http.post(
      `${BASE_URL}/api/v1/attendance/check-ins`,
      JSON.stringify({ work_mode: "onsite" }),
      {
        headers,
        tags: { request: "check_in" },
        responseCallback: CHECK_IN_EXPECTED,
      },
    );
  }

  check(response, {
    "check-in returns 201": (result) => result.status === 201,
  });
  return response.status === 201 ? response.json() : null;
}

export default function (data) {
  // The global iteration counter gives every synthetic user the same share of
  // the profile, regardless of how k6 schedules VUs. VU-id round-robin can be
  // badly skewed (a handful of hot VUs) and would exhaust those users'
  // mutation budgets even though the aggregate budget has 2× headroom.
  const session = data.sessions[exec.scenario.iterationInTest % data.sessions.length];
  const record = checkIn(session);

  if (record !== null) {
    const response = http.post(
      `${BASE_URL}/api/v1/attendance/records/${record.id}/check-out`,
      "{}",
      { headers: authHeaders(session), tags: { request: "check_out" } },
    );
    if (response.status !== 200) {
      console.error(
        `check-out ${response.status} user=${session.email} record=${record.id} ` +
          `record_employee=${record.employee_id}: ${response.body}`,
      );
    }
    check(response, {
      "check-out returns 200": (result) => result.status === 200,
    });
  }

  // One board read per second alongside the check-ins; reads are exempt from
  // the mutation tier. `iterationInTest` is global, unlike per-VU `__ITER`,
  // which would burst every VU's first iteration into simultaneous board reads.
  if (exec.scenario.iterationInTest % BOARD_READ_EVERY === 0) {
    const response = http.get(`${BASE_URL}/api/v1/attendance/board`, {
      headers: authHeaders(data.manager),
      tags: { request: "board" },
    });
    check(response, {
      "board returns 200": (result) => result.status === 200,
    });
  }
}
