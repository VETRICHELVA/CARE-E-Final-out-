// Shared sign-in for the three apps. The hub decides who may do what; these only decide what to show.
import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router";
import { client, login, logout, type Me, unwrap, useAuth } from "@care-e/api-client";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Field, FieldError, FieldGroup, FieldLabel } from "./components/ui/field";
import { Input } from "./components/ui/input";
import { ErrorState, Loading } from "./states";

export const useSignedIn = () => useAuth((s) => s.tokens !== null);

/** The signed-in user, their org and capabilities, from `/auth/me`. */
export function useMe() {
  return useQuery({
    queryKey: ["/api/v1/auth/me"],
    queryFn: () => unwrap(client.GET("/api/v1/auth/me")),
    enabled: useSignedIn(),
    // Read by both the route guard and the header; without this the header's mount refetches it.
    staleTime: 60_000,
  });
}

/** Whether to show an action. Display only: the hub still checks every call. */
export const can = (me: Me | undefined, capability: string) =>
  me?.capabilities.includes(capability) ?? false;

export const useCan = (capability: string) => can(useMe().data, capability);

export function LoginPage({ appName }: { appName: string }) {
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/";
  const signIn = useMutation({
    mutationFn: (form: FormData) => login(String(form.get("email")), String(form.get("password"))),
    onSuccess: () => navigate(from, { replace: true }),
  });
  if (useSignedIn() && !signIn.isPending) return <Navigate to={from} replace />;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    signIn.mutate(new FormData(event.currentTarget));
  };
  return (
    <main className="grid min-h-svh place-items-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in to {appName}</CardTitle>
          <CardDescription>CARE-E shortage-resolution network</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="email">Email</FieldLabel>
                <Input id="email" name="email" type="email" autoComplete="username" required />
              </Field>
              <Field>
                <FieldLabel htmlFor="password">Password</FieldLabel>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                />
              </Field>
              {signIn.error && <FieldError>{signIn.error.message}</FieldError>}
              <Button type="submit" disabled={signIn.isPending}>
                {signIn.isPending ? "Signing in…" : "Sign in"}
              </Button>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}

/** Signed-in users of an allowed org type see the app; anyone else sees `refusal`. */
export function ProtectedRoute({ allow, refusal }: { allow: string[]; refusal: string }) {
  const location = useLocation();
  const signedIn = useSignedIn();
  const me = useMe();
  if (!signedIn) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (me.isPending) return <Loading />;
  if (me.isError) return <ErrorState error={me.error} />;
  if (!allow.includes(me.data.org.type)) {
    return (
      <main className="grid min-h-svh place-items-center p-4">
        <Card className="w-full max-w-md" role="alert">
          <CardHeader>
            <CardTitle>{refusal}</CardTitle>
            <CardDescription>
              You are signed in as {me.data.user.full_name} of {me.data.org.name} (
              {me.data.org.type}).
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button onClick={() => void logout()}>Sign out</Button>
          </CardContent>
        </Card>
      </main>
    );
  }
  return <Outlet />;
}
