/**
 * Skills tab — catalog management (search, archived toggle, create, rename,
 * archive/restore) and per-employee rows (attach from the active catalog,
 * proficiency 1–5, update, remove). The generated api-client is mocked
 * wholesale; hooks, error mapping and components are the real thing.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useArchiveSkill,
  useCreateSkill,
  useGetCurrentEmployee,
  useListEmployeeSkills,
  useListEmployees,
  useListSkills,
  useRecordEmployeeSkill,
  useRemoveEmployeeSkill,
  useRestoreSkill,
  useUpdateEmployeeSkill,
  useUpdateSkill,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { SkillsPanel } from "@/features/performance/components/SkillsPanel";
import type { EmployeeSkill, ListSkillsParams, Skill } from "@/features/performance/types";

import {
  apiError,
  itemState,
  renderWithClient,
  requestPageState,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "../leave/helpers";

const auth = vi.hoisted(() => ({
  user: {
    role: "hr" as string,
    employee_id: null as string | null,
  },
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => ({ user: auth.user }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", async () =>
  (await import("./api-client-mock")).createPerformanceApiClientMock(),
);

const MANAGER_ID = "01900000-0000-7000-8000-0000000000a1";
const WENDY_ID = "01900000-0000-7000-8000-0000000000a2";
const TENANT_ID = "01900000-0000-7000-8000-000000000001";
const DEPARTMENT_ID = "01900000-0000-7000-8000-0000000000d1";
const POSITION_ID = "01900000-0000-7000-8000-0000000000e1";
const LOCATION_ID = "01900000-0000-7000-8000-0000000000f1";

const BASE_EMPLOYEE = {
  employee_code: "WP-0001",
  email: "person@workpulse.test",
  department_id: DEPARTMENT_ID,
  position_id: POSITION_ID,
  location_id: LOCATION_ID,
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2024-01-08",
  terminated_at: null,
} as const;

const MANAGER = {
  ...BASE_EMPLOYEE,
  id: MANAGER_ID,
  first_name: "Morgan",
  last_name: "Lee",
  email: "morgan.lee@workpulse.test",
} as const;

const WENDY = {
  ...BASE_EMPLOYEE,
  id: WENDY_ID,
  employee_code: "WP-0002",
  first_name: "Wendy",
  last_name: "Chen",
  email: "wendy.chen@workpulse.test",
  manager_id: MANAGER_ID,
} as const;

const MANAGER_FULL = {
  ...MANAGER,
  tenant_id: TENANT_ID,
  created_at: "2024-01-08T00:00:00Z",
  updated_at: "2024-01-08T00:00:00Z",
};

const REACT_SKILL: Skill = {
  id: "01900000-0000-7000-8000-0000000000c1",
  name: "React",
  description: "UI library",
  archived_at: null,
};

const TYPESCRIPT_SKILL: Skill = {
  id: "01900000-0000-7000-8000-0000000000c2",
  name: "TypeScript",
  description: null,
  archived_at: null,
};

const LEGACY_SKILL: Skill = {
  id: "01900000-0000-7000-8000-0000000000c3",
  name: "Legacy SQL",
  description: "Old warehouse queries",
  archived_at: "2026-08-01T09:00:00Z",
};

const REACT_ROW: EmployeeSkill = {
  skill_id: REACT_SKILL.id,
  skill_name: "React",
  proficiency: 3,
  note: "Owns the design system.",
  created_at: "2026-09-01T09:00:00Z",
  updated_at: "2026-09-01T09:00:00Z",
};

function skillPageState(items: Skill[], page = 1, totalPages = 1) {
  return {
    data: {
      data: {
        items,
        page: {
          page,
          page_size: 20,
          total_items: totalPages * 20,
          total_pages: totalPages,
        },
      },
      status: 200,
    },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

function employeeSkillsState(items: EmployeeSkill[]) {
  return {
    data: { data: { items }, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

function wireSkills({
  active = [REACT_SKILL, TYPESCRIPT_SKILL] as Skill[],
  archived = [REACT_SKILL, TYPESCRIPT_SKILL, LEGACY_SKILL] as Skill[],
} = {}) {
  (useListSkills as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (params?: ListSkillsParams) => {
      // `useActiveSkills` asks for the whole active catalog for the picker.
      if (params?.page_size === 100) return skillPageState(active);
      return skillPageState(params?.include_archived ? archived : active);
    },
  );
}

const createSkill = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateSkill = vi.fn<(variables: unknown) => Promise<unknown>>();
const archiveSkill = vi.fn<(variables: unknown) => Promise<unknown>>();
const restoreSkill = vi.fn<(variables: unknown) => Promise<unknown>>();
const recordSkill = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateEmployeeSkill = vi.fn<(variables: unknown) => Promise<unknown>>();
const removeEmployeeSkill = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { role: "hr", employee_id: null };
  wireSkills();
  wireQuery(useListEmployees, requestPageState([MANAGER, WENDY]));
  wireQuery(useGetCurrentEmployee, itemState(MANAGER_FULL));
  wireQuery(useListEmployeeSkills, employeeSkillsState([]));
  wireMutation(useCreateSkill as unknown as MutateFn, createSkill);
  wireMutation(useUpdateSkill as unknown as MutateFn, updateSkill);
  wireMutation(useArchiveSkill as unknown as MutateFn, archiveSkill);
  wireMutation(useRestoreSkill as unknown as MutateFn, restoreSkill);
  wireMutation(useRecordEmployeeSkill as unknown as MutateFn, recordSkill);
  wireMutation(useUpdateEmployeeSkill as unknown as MutateFn, updateEmployeeSkill);
  wireMutation(useRemoveEmployeeSkill as unknown as MutateFn, removeEmployeeSkill);
});

describe("SkillsPanel", () => {
  it("lists active catalog skills and reveals archived rows on request", async () => {
    const user = userEvent.setup();
    renderWithClient(<SkillsPanel />);

    expect(await screen.findByText("React")).toBeInTheDocument();
    expect(screen.getByText("TypeScript")).toBeInTheDocument();
    expect(screen.queryByText("Legacy SQL")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show archived" }));

    expect(await screen.findByText("Legacy SQL")).toBeInTheDocument();
    expect(screen.getByText("Archived")).toBeInTheDocument();
    await waitFor(() =>
      expect(useListSkills).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_archived: true }),
      ),
    );
  });

  it("creates a catalog skill", async () => {
    const user = userEvent.setup();
    createSkill.mockResolvedValue({ data: { status: 201 } });
    renderWithClient(<SkillsPanel />);

    await user.click(await screen.findByRole("button", { name: "New skill" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Skill"), "Incident response");
    await user.type(
      within(dialog).getByLabelText("Description (optional)"),
      "Run the first hour of an incident.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Create skill" }));

    await waitFor(() =>
      expect(createSkill).toHaveBeenCalledWith({
        data: {
          name: "Incident response",
          description: "Run the first hour of an incident.",
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Skill created");
  });

  it("keeps the name and shows the pinned copy on a duplicate name", async () => {
    const user = userEvent.setup();
    createSkill.mockRejectedValue(
      apiError(409, "skill_name_taken", "A skill with this name already exists"),
    );
    renderWithClient(<SkillsPanel />);

    await user.click(await screen.findByRole("button", { name: "New skill" }));
    const dialog = await screen.findByRole("dialog");
    const name = within(dialog).getByLabelText("Skill");
    await user.type(name, "React");
    await user.click(within(dialog).getByRole("button", { name: "Create skill" }));

    expect(
      await within(dialog).findByText(
        "A skill with this name already exists, including archived skills.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(name).toHaveValue("React");
  });

  it("archives and restores catalog skills after confirmation", async () => {
    const user = userEvent.setup();
    archiveSkill.mockResolvedValue({ data: { status: 200 } });
    restoreSkill.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<SkillsPanel />);

    await user.click(await screen.findByRole("button", { name: "Archive React" }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Archive skill" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Archive skill" }));
    await waitFor(() => expect(archiveSkill).toHaveBeenCalledWith({ id: REACT_SKILL.id }));
    expect(toast.success).toHaveBeenCalledWith("Skill archived");

    await user.click(screen.getByRole("button", { name: "Show archived" }));
    await user.click(await screen.findByRole("button", { name: "Restore Legacy SQL" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Restore skill" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Restore skill" }));
    await waitFor(() => expect(restoreSkill).toHaveBeenCalledWith({ id: LEGACY_SKILL.id }));
    expect(toast.success).toHaveBeenCalledWith("Skill restored");
  });

  it("attaches an active catalog skill with a proficiency and note", async () => {
    const user = userEvent.setup();
    auth.user = { role: "manager", employee_id: MANAGER_ID };
    recordSkill.mockResolvedValue({ data: { status: 201 } });
    renderWithClient(<SkillsPanel />);

    await user.click(await screen.findByRole("button", { name: "Add skill" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("combobox", { name: "Skill" }));
    expect(await screen.findByRole("option", { name: "React" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Legacy SQL" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "React" }));

    await user.click(within(dialog).getByRole("combobox", { name: "Proficiency" }));
    await user.click(await screen.findByRole("option", { name: "4 / 5" }));
    await user.type(
      within(dialog).getByLabelText("Note (optional)"),
      "Leads the design-system guild.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Add skill" }));

    await waitFor(() =>
      expect(recordSkill).toHaveBeenCalledWith({
        employeeId: MANAGER_ID,
        data: {
          skill_id: REACT_SKILL.id,
          proficiency: 4,
          note: "Leads the design-system guild.",
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Employee skill added");
  });

  it.each([
    ["skill_archived", "This skill is archived and can't be assigned to anyone new."],
    [
      "employee_skill_exists",
      "This employee already has this skill. Update the existing row instead.",
    ],
  ])("shows the pinned %s refusal on attach", async (code, copy) => {
    const user = userEvent.setup();
    auth.user = { role: "manager", employee_id: MANAGER_ID };
    recordSkill.mockRejectedValue(apiError(409, code, copy));
    renderWithClient(<SkillsPanel />);

    await user.click(await screen.findByRole("button", { name: "Add skill" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Skill" }));
    await user.click(await screen.findByRole("option", { name: "React" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Proficiency" }));
    await user.click(await screen.findByRole("option", { name: "4 / 5" }));
    await user.click(within(dialog).getByRole("button", { name: "Add skill" }));

    expect(await within(dialog).findByText(copy)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("updates an employee skill in place and removes it after confirmation", async () => {
    const user = userEvent.setup();
    auth.user = { role: "manager", employee_id: MANAGER_ID };
    wireQuery(useListEmployeeSkills, employeeSkillsState([REACT_ROW]));
    updateEmployeeSkill.mockResolvedValue({ data: { status: 200 } });
    removeEmployeeSkill.mockResolvedValue({ data: { status: 204 } });
    renderWithClient(<SkillsPanel />);

    await user.click(await screen.findByRole("button", { name: "Update React" }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Update skill" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("combobox", { name: "Skill" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("combobox", { name: "Proficiency" }));
    await user.click(await screen.findByRole("option", { name: "5 / 5" }));
    const note = within(dialog).getByLabelText("Note (optional)");
    await user.clear(note);
    await user.type(note, "Now mentors the guild.");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateEmployeeSkill).toHaveBeenCalledWith({
        employeeId: MANAGER_ID,
        skillId: REACT_SKILL.id,
        data: { proficiency: 5, note: "Now mentors the guild." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Employee skill updated");

    await user.click(await screen.findByRole("button", { name: "Remove React" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Remove skill" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove skill" }));

    await waitFor(() =>
      expect(removeEmployeeSkill).toHaveBeenCalledWith({
        employeeId: MANAGER_ID,
        skillId: REACT_SKILL.id,
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Employee skill removed");
  });

  it("lets an Employee record their own skills but manage no catalog", async () => {
    auth.user = { role: "employee", employee_id: WENDY_ID };
    wireQuery(useGetCurrentEmployee, itemState({ ...WENDY, tenant_id: TENANT_ID }));
    wireQuery(useListEmployeeSkills, employeeSkillsState([]));
    renderWithClient(<SkillsPanel />);

    expect(await screen.findByText("React")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New skill" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive React" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Choose an employee" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add skill" })).toBeInTheDocument();
    await waitFor(() =>
      expect(useListEmployeeSkills).toHaveBeenLastCalledWith(WENDY_ID, expect.anything()),
    );
  });

  it("shows Finance their own skills read-only", async () => {
    auth.user = { role: "finance", employee_id: WENDY_ID };
    wireQuery(useGetCurrentEmployee, itemState({ ...WENDY, tenant_id: TENANT_ID }));
    wireQuery(useListEmployeeSkills, employeeSkillsState([REACT_ROW]));
    renderWithClient(<SkillsPanel />);

    expect(await screen.findByText("Owns the design system.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add skill" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update React" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove React" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New skill" })).not.toBeInTheDocument();
  });

  it("gives an HR viewer without an employee link a subject picker", async () => {
    const user = userEvent.setup();
    renderWithClient(<SkillsPanel />);

    expect(await screen.findByRole("combobox", { name: "Choose an employee" })).toBeInTheDocument();
    await waitFor(() =>
      expect(useListEmployeeSkills).toHaveBeenLastCalledWith(MANAGER_ID, expect.anything()),
    );

    await user.click(screen.getByRole("combobox", { name: "Choose an employee" }));
    await user.click(await screen.findByRole("option", { name: "Wendy Chen" }));
    await waitFor(() =>
      expect(useListEmployeeSkills).toHaveBeenLastCalledWith(WENDY_ID, expect.anything()),
    );
  });
});
