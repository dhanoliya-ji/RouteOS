/**
 * Tests for the shared UI primitives.
 *
 * Presentational components, so these assert on what a user would see and do
 * rather than on internals — the two Modal behaviours in particular are the
 * kind that break silently and are only noticed as "the form kept my last
 * entry" or "the dialog closes when I click in it".
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmptyState, ErrorState, KpiCard, Modal, Skeleton, StatusBadge, ToastHost } from "./ui";
import { useToast } from "../stores/toast";

describe("StatusBadge", () => {
  it("renders the value with underscores replaced", () => {
    // So the wire format never reaches the user.
    render(<StatusBadge value="OUT_FOR_DELIVERY" />);
    expect(screen.getByText("OUT FOR DELIVERY")).toBeInTheDocument();
  });

  it("colours a known status", () => {
    render(<StatusBadge value="DELIVERED" />);
    expect(screen.getByText("DELIVERED").className).toContain("green");
  });

  it("falls back to neutral for an unknown value rather than rendering unstyled", () => {
    /**
     * The degradation path: a status the backend adds later still renders as a
     * badge, in grey, instead of as bare text that looks broken.
     */
    render(<StatusBadge value="SOME_NEW_STATUS" />);
    const el = screen.getByText("SOME NEW STATUS");
    expect(el.className).toContain("badge");
    expect(el.className).toContain("ink");
  });

  it("serves four different enums from one table", () => {
    // Order status, vehicle status, route status and priority share the map —
    // which works only because none of their values collide.
    for (const v of ["PENDING", "IN_TRANSIT", "COMPLETED", "URGENT"]) {
      const { unmount } = render(<StatusBadge value={v} />);
      expect(screen.getByText(v.replace(/_/g, " ")).className).not.toContain("ink-100");
      unmount();
    }
  });
});

describe("KpiCard", () => {
  it("renders label, value and optional hint", () => {
    render(<KpiCard label="Pending" value={42} hint="awaiting a plan" />);
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("awaiting a plan")).toBeInTheDocument();
  });

  it("omits the hint when there is none", () => {
    const { container } = render(<KpiCard label="Pending" value={0} />);
    // Only the label and the value.
    expect(container.textContent).toBe("Pending0");
  });

  it("accepts a node as the value, not just a string", () => {
    render(<KpiCard label="Distance" value={<span data-testid="v">12.5 km</span>} />);
    expect(screen.getByTestId("v")).toHaveTextContent("12.5 km");
  });
});

describe("EmptyState and ErrorState say different things", () => {
  it("EmptyState reports an absence", () => {
    render(<EmptyState title="No orders found" hint="Try a different filter." />);
    expect(screen.getByText("No orders found")).toBeInTheDocument();
    expect(screen.getByText("Try a different filter.")).toBeInTheDocument();
  });

  it("ErrorState reports a failure", () => {
    /**
     * Currently unused by any page — no screen checks a query's isError, so a
     * failed request falls through to the empty branch and reads as "no
     * orders". The component works; it is the wiring that is missing.
     */
    render(<ErrorState message="Could not load orders" />);
    expect(screen.getByText("Could not load orders")).toBeInTheDocument();
  });
});

describe("Skeleton", () => {
  it("takes its size from the caller so the layout does not jump", () => {
    const { container } = render(<Skeleton className="h-64" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain("h-64");
    expect(el.className).toContain("animate-pulse");
  });
});

describe("Modal", () => {
  it("renders nothing at all when closed", () => {
    render(
      <Modal open={false} onClose={() => {}} title="New Order">
        <p>form fields</p>
      </Modal>,
    );
    expect(screen.queryByText("New Order")).not.toBeInTheDocument();
    expect(screen.queryByText("form fields")).not.toBeInTheDocument();
  });

  it("UNMOUNTS its children when closed, so form state resets", async () => {
    /**
     * The behaviour a user notices when it breaks. Because the modal returns
     * null rather than hiding with CSS, the children unmount and their state
     * goes with them — which is what stops the order you edited last time
     * appearing when you next click "New Order".
     */
    function Field() {
      return <input aria-label="Customer" defaultValue="" />;
    }
    const { rerender } = render(
      <Modal open onClose={() => {}} title="Edit">
        <Field />
      </Modal>,
    );

    await userEvent.type(screen.getByLabelText("Customer"), "typed but abandoned");
    expect(screen.getByLabelText("Customer")).toHaveValue("typed but abandoned");

    // Close, then reopen.
    rerender(
      <Modal open={false} onClose={() => {}} title="Edit">
        <Field />
      </Modal>,
    );
    rerender(
      <Modal open onClose={() => {}} title="Edit">
        <Field />
      </Modal>,
    );

    expect(screen.getByLabelText("Customer")).toHaveValue("");
  });

  it("closes when the backdrop is clicked", async () => {
    const onClose = vi.fn();
    const { container } = render(
      <Modal open onClose={onClose} title="New Order">
        <p>body</p>
      </Modal>,
    );
    await userEvent.click(container.firstElementChild as HTMLElement);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does NOT close when the panel itself is clicked", async () => {
    /**
     * Without stopPropagation on the panel, a click anywhere in the form would
     * bubble to the backdrop and dismiss it mid-edit — losing everything
     * typed, which is the worst possible failure for a form.
     */
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="New Order">
        <p>body text</p>
      </Modal>,
    );
    await userEvent.click(screen.getByText("body text"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes from the × button", async () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="New Order">
        <p>body</p>
      </Modal>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("ToastHost", () => {
  it("renders nothing when the queue is empty", () => {
    useToast.setState({ toasts: [] });
    const { container } = render(<ToastHost />);
    expect(container.firstElementChild?.children).toHaveLength(0);
  });

  it("renders queued toasts and styles them by kind", () => {
    useToast.setState({
      toasts: [
        { id: 1, message: "Saved", kind: "success" },
        { id: 2, message: "Failed", kind: "error" },
      ],
    });
    render(<ToastHost />);
    expect(screen.getByText("Saved").className).toContain("green");
    expect(screen.getByText("Failed").className).toContain("red");
  });

  it("sits above the modal, so a form's own error is visible", () => {
    // z-[1000] against Modal's z-[900]. Reversed, a toast raised by a form's
    // mutation would render behind that form.
    useToast.setState({ toasts: [{ id: 1, message: "x", kind: "info" }] });
    const { container } = render(<ToastHost />);
    expect((container.firstElementChild as HTMLElement).className).toContain("z-[1000]");
  });
});
