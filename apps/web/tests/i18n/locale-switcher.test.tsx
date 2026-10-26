/**
 * Locale switcher — choosing a language persists the cookie through the server
 * action and refreshes the route so Server Components re-render in the new
 * locale, without changing the URL.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "@/i18n/actions";
import { LocaleSwitcher } from "@/components/locale-switcher";

const routerMocks = vi.hoisted(() => ({
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMocks,
}));

vi.mock("@/i18n/actions", () => ({
  setLocale: vi.fn(),
}));

const mockSetLocale = vi.mocked(setLocale);

describe("LocaleSwitcher", () => {
  beforeEach(() => {
    mockSetLocale.mockReset();
    mockSetLocale.mockResolvedValue();
    routerMocks.refresh.mockReset();
  });

  it("persists a different locale and refreshes the route", async () => {
    const user = userEvent.setup();
    render(<LocaleSwitcher />);

    await user.click(screen.getByRole("button", { name: "Language" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Tiếng Việt" }));

    await waitFor(() => expect(mockSetLocale).toHaveBeenCalledWith("vi"));
    expect(routerMocks.refresh).toHaveBeenCalled();
  });

  it("ignores the already-active locale", async () => {
    const user = userEvent.setup();
    render(<LocaleSwitcher />);

    await user.click(screen.getByRole("button", { name: "Language" }));
    await user.click(screen.getByRole("menuitemradio", { name: "English" }));

    expect(mockSetLocale).not.toHaveBeenCalled();
    expect(routerMocks.refresh).not.toHaveBeenCalled();
  });
});
