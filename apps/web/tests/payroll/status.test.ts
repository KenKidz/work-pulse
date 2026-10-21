/**
 * Payroll status gating (AC14). Finalize is only available on `calculated`;
 * recalculate only on `calculated`/`failed`; bonuses only while a run still has
 * a provisional payslip set; queued/calculating runs are the only polled ones.
 */

import { describe, expect, it } from "vitest";

import {
  canAddBonus,
  canFinalizeRun,
  canRecalculateRun,
  isRunCalculated,
  isRunInProgress,
} from "@/features/payroll/labels";
import type { PayrollRunStatus } from "@/features/payroll/types";

const ALL: PayrollRunStatus[] = ["queued", "calculating", "calculated", "finalized", "failed"];

describe("payroll run status gates", () => {
  it("enables finalize only from calculated", () => {
    expect(ALL.filter(canFinalizeRun)).toEqual(["calculated"]);
  });

  it("enables recalculate from calculated or failed", () => {
    expect(ALL.filter(canRecalculateRun)).toEqual(["calculated", "failed"]);
  });

  it("enables bonuses only while the run is calculated", () => {
    expect(ALL.filter(canAddBonus)).toEqual(["calculated"]);
  });

  it("polls only queued and calculating runs", () => {
    expect(ALL.filter(isRunInProgress)).toEqual(["queued", "calculating"]);
  });

  it("treats calculated and finalized as having result tables", () => {
    expect(ALL.filter(isRunCalculated)).toEqual(["calculated", "finalized"]);
  });
});
