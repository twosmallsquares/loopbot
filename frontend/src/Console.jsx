import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Lock,
  Search,
  Shield,
  ShieldOff,
  RefreshCw,
  Home as HomeIcon,
  UserX,
  Eye,
  EyeOff,
} from "lucide-react";
import { Link } from "react-router-dom";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const PASS_KEY = "quintuple_admin_pwd";

export default function Console() {
  const [password, setPassword] = useState(() => sessionStorage.getItem(PASS_KEY) || "");
  const [authed, setAuthed] = useState(false);
  const [authing, setAuthing] = useState(false);
  const [showPwd, setShowPwd] = useState(false);

  const [logs, setLogs] = useState([]);
  const [blacklist, setBlacklist] = useState([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);

  const [newUserId, setNewUserId] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [newReason, setNewReason] = useState("");
  const [adding, setAdding] = useState(false);

  const login = async () => {
    setAuthing(true);
    try {
      await axios.post(`${API}/admin/verify`, { password });
      sessionStorage.setItem(PASS_KEY, password);
      setAuthed(true);
      toast.success("Welcome, admin.");
    } catch (e) {
      toast.error("Wrong password");
      setPassword("");
      sessionStorage.removeItem(PASS_KEY);
    } finally {
      setAuthing(false);
    }
  };

  const refresh = async () => {
    setLoading(true);
    try {
      const [l, b] = await Promise.all([
        axios.get(`${API}/admin/logs`, { params: { password, limit: 500 } }),
        axios.get(`${API}/admin/blacklist`, { params: { password } }),
      ]);
      setLogs(l.data.logs || []);
      setBlacklist(b.data.blacklist || []);
    } catch (e) {
      toast.error("Load failed: " + (e.response?.data?.detail || e.message));
      if (e.response?.status === 401) {
        setAuthed(false);
        sessionStorage.removeItem(PASS_KEY);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Auto-login if password is already in session
    if (password && !authed) {
      (async () => {
        try {
          await axios.post(`${API}/admin/verify`, { password });
          setAuthed(true);
        } catch {
          sessionStorage.removeItem(PASS_KEY);
          setPassword("");
        }
      })();
    }
  }, []);  // eslint-disable-line

  useEffect(() => {
    if (authed) {
      refresh();
      const t = setInterval(refresh, 8000);
      return () => clearInterval(t);
    }
  }, [authed]);  // eslint-disable-line

  const addBlacklist = async (preset) => {
    const userId = preset?.user_id || newUserId.trim();
    const username = preset?.username || newUsername.trim() || undefined;
    const reason = preset?.reason || newReason.trim() || undefined;
    if (!userId) {
      toast.error("user_id required");
      return;
    }
    setAdding(true);
    try {
      await axios.post(
        `${API}/admin/blacklist?password=${encodeURIComponent(password)}`,
        { user_id: userId, username, reason }
      );
      toast.success(`Blacklisted ${username || userId}`);
      setNewUserId("");
      setNewUsername("");
      setNewReason("");
      refresh();
    } catch (e) {
      toast.error("Failed: " + (e.response?.data?.detail || e.message));
    } finally {
      setAdding(false);
    }
  };

  const removeBlacklist = async (userId) => {
    try {
      await axios.delete(
        `${API}/admin/blacklist/${userId}?password=${encodeURIComponent(password)}`
      );
      toast.success(`Unblacklisted ${userId}`);
      refresh();
    } catch (e) {
      toast.error("Failed: " + (e.response?.data?.detail || e.message));
    }
  };

  const filteredLogs = logs.filter((l) => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return (
      (l.username || "").toLowerCase().includes(q) ||
      (l.user_id || "").toLowerCase().includes(q) ||
      (l.guild_id || "").toLowerCase().includes(q) ||
      (l.channel_id || "").toLowerCase().includes(q) ||
      (l.message || "").toLowerCase().includes(q) ||
      (l.command || "").toLowerCase().includes(q)
    );
  });

  const blacklistedIds = new Set(blacklist.map((b) => b.user_id));

  if (!authed) {
    return (
      <div className="min-h-screen bg-zinc-950 text-zinc-100" data-testid="console-locked">
        <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_30%_-10%,rgba(16,185,129,0.10),transparent_40%)]" />
        <div className="relative mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6">
          <Card className="w-full border-zinc-800 bg-zinc-900/40">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 font-mono text-zinc-100">
                <Lock className="h-4 w-4 text-emerald-400" />
                Admin console
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-zinc-400">
                Enter the admin password to view command logs and manage the blacklist.
              </p>
              <div className="relative">
                <Input
                  data-testid="admin-password-input"
                  type={showPwd ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && login()}
                  placeholder="password"
                  className="border-zinc-800 bg-zinc-950 pr-9 font-mono text-zinc-100"
                />
                <button
                  type="button"
                  onClick={() => setShowPwd(!showPwd)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
                  data-testid="toggle-password-visibility"
                >
                  {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <Button
                onClick={login}
                disabled={authing || !password}
                className="w-full bg-emerald-500 text-zinc-950 hover:bg-emerald-400"
                data-testid="admin-login-btn"
              >
                {authing ? (
                  <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Lock className="mr-2 h-4 w-4" />
                )}
                Unlock
              </Button>
              <Link
                to="/"
                className="block text-center text-xs text-zinc-500 hover:text-zinc-300"
              >
                ← back to dashboard
              </Link>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100" data-testid="console-page">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_30%_-10%,rgba(16,185,129,0.10),transparent_40%)]" />

      <div className="relative mx-auto max-w-7xl px-6 py-10">
        <header className="flex items-center justify-between" data-testid="console-header">
          <div className="flex items-center gap-3">
            <Shield className="h-5 w-5 text-emerald-400" />
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.22em] text-zinc-500">
                quintuple · admin
              </p>
              <h1 className="font-mono text-xl font-semibold text-zinc-100">console</h1>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className="border-emerald-500/30 bg-emerald-500/10 font-mono text-xs text-emerald-300"
            >
              {logs.length} log{logs.length === 1 ? "" : "s"} · {blacklist.length} blocked
            </Badge>
            <Link to="/">
              <Button
                variant="outline"
                size="sm"
                className="border-zinc-800 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
              >
                <HomeIcon className="mr-2 h-4 w-4" /> Dashboard
              </Button>
            </Link>
          </div>
        </header>

        <div className="mt-10 grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Logs */}
          <Card className="border-zinc-800 bg-zinc-900/40 lg:col-span-2" data-testid="logs-card">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
              <CardTitle className="text-zinc-100">Command log</CardTitle>
              <Button
                onClick={refresh}
                disabled={loading}
                size="sm"
                variant="outline"
                className="border-zinc-800 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
                data-testid="refresh-logs-btn"
              >
                <RefreshCw className={"mr-2 h-4 w-4 " + (loading ? "animate-spin" : "")} />
                Refresh
              </Button>
            </CardHeader>
            <CardContent>
              <div className="relative mb-4">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" />
                <Input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="filter by user, guild, message…"
                  className="border-zinc-800 bg-zinc-950 pl-9 font-mono text-sm text-zinc-100"
                  data-testid="filter-input"
                />
              </div>

              <div className="max-h-[640px] overflow-y-auto">
                <table className="w-full text-sm" data-testid="logs-table">
                  <thead className="sticky top-0 bg-zinc-900/95 backdrop-blur">
                    <tr className="border-b border-zinc-800 text-left font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                      <th className="py-2 pr-3">When</th>
                      <th className="py-2 pr-3">User</th>
                      <th className="py-2 pr-3">Where</th>
                      <th className="py-2 pr-3">Cmd</th>
                      <th className="py-2 pr-3">Message</th>
                      <th className="py-2 pr-3"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredLogs.length === 0 && (
                      <tr>
                        <td colSpan="6" className="py-8 text-center text-sm text-zinc-500">
                          {loading ? "Loading…" : "No logs."}
                        </td>
                      </tr>
                    )}
                    {filteredLogs.map((l) => {
                      const t = l.timestamp ? new Date(l.timestamp) : null;
                      const blacked = blacklistedIds.has(l.user_id);
                      return (
                        <tr
                          key={l.id}
                          className="border-b border-zinc-900/60 align-top hover:bg-zinc-900/40"
                          data-testid={`log-row-${l.id}`}
                        >
                          <td className="py-2 pr-3 font-mono text-[11px] text-zinc-500">
                            {t ? t.toLocaleString() : "—"}
                          </td>
                          <td className="py-2 pr-3">
                            <div className="font-mono text-xs text-emerald-300">
                              @{l.username || "unknown"}
                            </div>
                            <div className="font-mono text-[10px] text-zinc-600">
                              {l.user_id || "—"}
                            </div>
                          </td>
                          <td className="py-2 pr-3 font-mono text-[10px] text-zinc-500">
                            <div>guild: {l.guild_id || "DM"}</div>
                            <div>chan: {l.channel_id || "—"}</div>
                          </td>
                          <td className="py-2 pr-3">
                            <code className="rounded bg-zinc-950 px-1.5 py-0.5 font-mono text-[11px] text-emerald-300">
                              /{l.command || "?"}
                            </code>
                          </td>
                          <td className="py-2 pr-3 max-w-xs truncate text-zinc-300" title={l.message}>
                            {l.message}
                          </td>
                          <td className="py-2 pr-1 text-right">
                            {blacked ? (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => removeBlacklist(l.user_id)}
                                className="h-7 border-emerald-500/30 bg-emerald-500/10 px-2 text-xs text-emerald-300 hover:bg-emerald-500/20"
                                data-testid={`unblock-${l.user_id}`}
                              >
                                <ShieldOff className="mr-1 h-3 w-3" /> Unblock
                              </Button>
                            ) : (
                              l.user_id && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() =>
                                    addBlacklist({
                                      user_id: l.user_id,
                                      username: l.username,
                                    })
                                  }
                                  className="h-7 border-red-500/30 bg-red-500/10 px-2 text-xs text-red-300 hover:bg-red-500/20"
                                  data-testid={`block-${l.user_id}`}
                                >
                                  <UserX className="mr-1 h-3 w-3" /> Block
                                </Button>
                              )
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* Blacklist */}
          <Card className="border-zinc-800 bg-zinc-900/40" data-testid="blacklist-card">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-zinc-100">
                <UserX className="h-4 w-4 text-red-400" /> Blacklist
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Input
                  value={newUserId}
                  onChange={(e) => setNewUserId(e.target.value)}
                  placeholder="user_id (snowflake)"
                  className="border-zinc-800 bg-zinc-950 font-mono text-sm"
                  data-testid="new-userid-input"
                />
                <Input
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  placeholder="username (optional)"
                  className="border-zinc-800 bg-zinc-950 font-mono text-sm"
                  data-testid="new-username-input"
                />
                <Input
                  value={newReason}
                  onChange={(e) => setNewReason(e.target.value)}
                  placeholder="reason (optional)"
                  className="border-zinc-800 bg-zinc-950 text-sm"
                  data-testid="new-reason-input"
                />
                <Button
                  onClick={() => addBlacklist()}
                  disabled={adding || !newUserId}
                  className="w-full bg-red-500 text-zinc-950 hover:bg-red-400"
                  data-testid="add-blacklist-btn"
                >
                  <UserX className="mr-2 h-4 w-4" /> Add to blacklist
                </Button>
              </div>

              <div className="max-h-[420px] space-y-2 overflow-y-auto" data-testid="blacklist-list">
                {blacklist.length === 0 && (
                  <p className="py-6 text-center text-sm text-zinc-500">No one's blacklisted.</p>
                )}
                {blacklist.map((b) => (
                  <div
                    key={b.user_id}
                    className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"
                    data-testid={`blacklist-item-${b.user_id}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-xs text-red-300">
                          @{b.username || "unknown"}
                        </div>
                        <div className="truncate font-mono text-[10px] text-zinc-600">
                          {b.user_id}
                        </div>
                        {b.reason && (
                          <div className="mt-1 text-xs text-zinc-400">{b.reason}</div>
                        )}
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => removeBlacklist(b.user_id)}
                        className="h-7 shrink-0 border-zinc-800 bg-zinc-900 px-2 text-xs text-zinc-300 hover:bg-zinc-800"
                        data-testid={`remove-blacklist-${b.user_id}`}
                      >
                        <ShieldOff className="mr-1 h-3 w-3" /> Remove
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
