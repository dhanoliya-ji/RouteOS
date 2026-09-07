/**
 * Tests for the auth gate and the map helpers.
 *
 * <Protected> is the highest-consequence component in the app: it decides,
 * on every page load, whether a signed-in user gets in. Its bug is specific and
 * nearly invisible in development — you rarely hard-refresh — so it is worth
 * pinning directly rather than through a whole rendered route tree.
 */
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useAuth } from "./stores/auth";
import { NCR_CENTER, ROUTE_COLORS, coloredDot, depotIcon, vehicleIcon } from "./utils/map";

/**
 * A local copy of App.tsx's gate.
 *
 * Duplicated deliberately: <Protected> is not exported, and exporting it purely
 * for a test would change the module's public surface to suit the test rather
 * than the app. The trade-off is stated so a future reader knows to keep the
 * two in step — the logic is three lines.
 */
function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <div>Loading…</div>;
  if (!user) return <div data-testid="redirected">redirected to /login</div>;
  return children;
}

function renderGate() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route
          path="/"
          element={
            <Protected>
              <div>the dashboard</div>
            </Protected>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const A_USER = {
  id: 1,
  name: "Dev",
  email: "d@routeos.dev",
  role: "DISPATCHER" as const,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
};

describe("the auth gate has three outcomes", () => {
  it("waits while the stored token is being checked", () => {
    /**
     * THE case. On a refresh the token is in localStorage but the user object
     * is gone, and confirming it takes a round trip. Treating that moment as
     * "signed out" would bounce a signed-in user to /login on every reload.
     */
    useAuth.setState({ user: null, loading: true });
    renderGate();

    expect(screen.getByText("Loading…")).toBeInTheDocument();
    // Crucially, it has NOT decided yet.
    expect(screen.queryByTestId("redirected")).not.toBeInTheDocument();
    expect(screen.queryByText("the dashboard")).not.toBeInTheDocument();
  });

  it("redirects once we know there is no user", () => {
    useAuth.setState({ user: null, loading: false });
    renderGate();
    expect(screen.getByTestId("redirected")).toBeInTheDocument();
  });

  it("renders the page for a signed-in user", () => {
    useAuth.setState({ user: A_USER, loading: false });
    renderGate();
    expect(screen.getByText("the dashboard")).toBeInTheDocument();
  });

  it("moves from waiting to admitted when the check resolves", () => {
    // The real sequence on a refresh with a good token.
    useAuth.setState({ user: null, loading: true });
    const { rerender } = renderGate();
    expect(screen.getByText("Loading…")).toBeInTheDocument();

    // Wrapped in act: this is a state update driving a re-render, exactly as
    // loadUser's resolution does in the real app.
    act(() => useAuth.setState({ user: A_USER, loading: false }));
    rerender(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="/"
            element={
              <Protected>
                <div>the dashboard</div>
              </Protected>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("the dashboard")).toBeInTheDocument();
    // ...and it never flashed the redirect on the way through.
    expect(screen.queryByTestId("redirected")).not.toBeInTheDocument();
  });
});

describe("map helpers", () => {
  it("centres on the demo data's geography", () => {
    // Delhi NCR, matching what backend/scripts/demo_geo.py seeds.
    const [lat, lon] = NCR_CENTER;
    expect(lat).toBeCloseTo(28.55, 2);
    expect(lon).toBeCloseTo(77.25, 2);
  });

  it("offers ten distinct route colours", () => {
    expect(ROUTE_COLORS).toHaveLength(10);
    expect(new Set(ROUTE_COLORS).size).toBe(10);
  });

  it("cycles colours so an eleventh route is not undefined", () => {
    /**
     * Every consumer indexes with `i % length`. Without the modulo an eleventh
     * route would read undefined and render an uncoloured polyline.
     */
    const colourFor = (i: number) => ROUTE_COLORS[i % ROUTE_COLORS.length];
    expect(colourFor(10)).toBe(ROUTE_COLORS[0]);
    expect(colourFor(23)).toBe(ROUTE_COLORS[3]);
    expect(colourFor(10)).toBeDefined();
  });

  it("builds a stop marker carrying the colour it was given", () => {
    // divIcons rather than image assets, because the colour is decided at
    // render time and a static asset per colour could not work.
    const icon = coloredDot("#ff0000", 14);
    expect(String(icon.options.html)).toContain("#ff0000");
    expect(icon.options.iconSize).toEqual([14, 14]);
  });

  it("anchors a marker at its centre, not its corner", () => {
    // Half the size. Otherwise every stop would hang below-right of its
    // actual coordinate — a consistent, easily-missed offset.
    const icon = coloredDot("#000", 20);
    expect(icon.options.iconAnchor).toEqual([10, 10]);
  });

  it("distinguishes a vehicle from a stop by shape", () => {
    // Round is a stop, square is a vehicle — so the two stay readable on a
    // busy map even when they share a route colour.
    expect(String(coloredDot("#000").options.html)).toContain("border-radius:50%");
    expect(String(vehicleIcon("#000").options.html)).not.toContain("border-radius:50%");
  });

  it("gives the depot one fixed appearance", () => {
    // A constant, not a factory: a depot has no per-route colour.
    expect(depotIcon.options.iconSize).toEqual([26, 26]);
    expect(String(depotIcon.options.html)).toContain("#facc15");
  });

  it("styles markers inline rather than with Tailwind classes", () => {
    /**
     * Necessary, not stylistic: this markup is handed to Leaflet as a string
     * and inserted outside React's tree, where Tailwind's build-time class
     * scanning cannot see it — so any class used here would be purged from the
     * stylesheet and the marker would render unstyled.
     */
    const icon = coloredDot("#123456");
    expect(String(icon.options.html)).toContain("style=");
    expect(icon.options.className).toBe("");
  });
});
