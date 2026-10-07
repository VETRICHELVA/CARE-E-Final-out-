import { QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { BrowserRouter, NavLink, Outlet, Route, Routes } from "react-router";
import { createQueryClient, logout, useAuth } from "@care-e/api-client";
import { can, LoginPage, ProtectedRoute, useMe } from "./auth";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Toaster } from "./components/ui/sonner";
import { cn } from "./lib/utils";
import { EmptyState } from "./states";

export type NavItem = {
  to: string;
  label: string;
  /** Hide the entry from users without this capability (display only; the hub still checks). */
  capability?: string;
  /** The screen; a placeholder until its section lands. */
  element?: ReactNode;
};

/** A screen that has no nav entry, e.g. `/shortages/:id`. */
export type ExtraRoute = { path: string; element: ReactNode };

export type AppConfig = {
  /** Shown in the header and on the sign-in page, e.g. "CARE-E Hospital". */
  name: string;
  /** Org types this app is for (`/auth/me` → org.type). */
  allow: string[];
  /** Shown to a signed-in user of any other org type. */
  refusal: string;
  nav: NavItem[];
  routes?: ExtraRoute[];
};

/** The header shows the user, their organization and its type (apps-ai-iot.md, Shared rules). */
function AppShell({ name, nav }: Pick<AppConfig, "name" | "nav">) {
  const me = useMe().data;
  const visible = nav.filter((item) => !item.capability || can(me, item.capability));
  return (
    <div className="min-h-svh">
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <span className="font-semibold text-primary">{name}</span>
          <nav className="flex flex-wrap gap-1 text-sm">
            {visible.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-2.5 py-1.5 text-muted-foreground hover:bg-accent",
                    isActive && "bg-accent font-medium text-accent-foreground",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          {me && (
            <div className="ml-auto flex items-center gap-3 text-sm">
              <div className="text-right leading-tight">
                <div className="font-medium">{me.user.full_name}</div>
                <div className="text-muted-foreground" data-testid="org-name">
                  {me.org.name}
                </div>
              </div>
              <Badge variant="secondary">{me.org.type}</Badge>
              <Button variant="outline" size="sm" onClick={() => void logout()}>
                Sign out
              </Button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-6xl p-4">
        <Outlet />
      </main>
    </div>
  );
}

const placeholder = (label: string) => (
  <EmptyState title={label}>This screen arrives in a later section.</EmptyState>
);

/** One app: sign-in, the org-type gate, the header and nav, and its screens. */
export function CareApp({ name, allow, refusal, nav, routes = [] }: AppConfig) {
  const [queryClient] = useState(createQueryClient);
  // Signing out (or a failed refresh) must not leave the last user's data in the cache.
  useEffect(
    () => useAuth.subscribe((s) => s.tokens === null && queryClient.clear()),
    [queryClient],
  );
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage appName={name} />} />
          <Route element={<ProtectedRoute allow={allow} refusal={refusal} />}>
            <Route element={<AppShell name={name} nav={nav} />}>
              {nav.map((item) => (
                <Route
                  key={item.to}
                  path={item.to}
                  element={item.element ?? placeholder(item.label)}
                />
              ))}
              {routes.map((route) => (
                <Route key={route.path} path={route.path} element={route.element} />
              ))}
              <Route path="*" element={<EmptyState title="Page not found" />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster />
    </QueryClientProvider>
  );
}
