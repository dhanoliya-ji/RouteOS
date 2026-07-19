import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../stores/auth";
import { ApiError } from "../api/client";

const DEMO = [
  { role: "Dispatcher", email: "dispatcher@routeos.dev", password: "dispatch12345" },
  { role: "Admin", email: "admin@routeos.dev", password: "admin12345" },
  { role: "Viewer", email: "viewer@routeos.dev", password: "viewer12345" },
];

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("dispatcher@routeos.dev");
  const [password, setPassword] = useState("dispatch12345");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-ink-900 to-brand-900 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center text-white">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-brand-600 text-xl font-bold">
            R
          </div>
          <h1 className="text-2xl font-semibold">RouteOS</h1>
          <p className="text-sm text-white/60">Intelligent logistics & fleet optimization</p>
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6">
          <div>
            <label className="label">Email</label>
            <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} type="email" />
          </div>
          <div>
            <label className="label">Password</label>
            <input
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
            />
          </div>
          {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <div className="mt-4 card p-4">
          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-400">Demo accounts</div>
          <div className="space-y-1">
            {DEMO.map((d) => (
              <button
                key={d.email}
                onClick={() => {
                  setEmail(d.email);
                  setPassword(d.password);
                }}
                className="flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-sm hover:bg-ink-100"
              >
                <span className="font-medium">{d.role}</span>
                <span className="text-ink-400">{d.email}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
