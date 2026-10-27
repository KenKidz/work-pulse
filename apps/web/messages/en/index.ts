/**
 * English catalog — the source of truth for the message shape. Every surface
 * owns one file; the index is the only place they are merged.
 */

import attendance from "./attendance.json";
import auth from "./auth.json";
import common from "./common.json";
import dashboard from "./dashboard.json";
import employees from "./employees.json";
import home from "./home.json";
import leave from "./leave.json";
import metadata from "./metadata.json";
import nav from "./nav.json";
import notifications from "./notifications.json";
import organizations from "./organizations.json";
import performance from "./performance.json";
import realtime from "./realtime.json";
import recruitment from "./recruitment.json";
import reports from "./reports.json";
import overtime from "./overtime.json";
import payroll from "./payroll.json";
import users from "./users.json";

const en = {
  attendance,
  auth,
  common,
  dashboard,
  employees,
  home,
  leave,
  metadata,
  nav,
  realtime,
  recruitment,
  reports,
  notifications,
  organizations,
  overtime,
  performance,
  payroll,
  users,
};

export default en;
