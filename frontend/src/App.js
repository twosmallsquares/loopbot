import { useEffect, useState } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import axios from "axios";
import { Toaster, toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Copy,
  Check,
  Zap,
  Link2,
  Send,
  BookOpen,
  RefreshCw,
  Power,
  PowerOff,
  AlertTriangle,
} from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const INTERACTIONS_URL = `${BACKEND_URL}/api/discord/interactions`;

const CopyField = ({ label, value, testId }) => {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    toast.success("Copied to clipboard");
    setTimeout(() => setCopied(false), 1400);
  };
  return (
    <div className="space-y-2" data-testid={`copy-field-${testId}`}>
      <p className="text-xs uppercase tracking-[0.18em] text-zinc-500">{label}</p>
      <div className="flex items-stretch gap-2">
        <code className="flex-1 truncate rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 font-mono text-sm text-emerald-300">
          {value}
        </code>
        <Button
          variant="outline"
          size="sm"
          onClick={copy}
          className="border-zinc-800 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
          data-testid={`copy-btn-${testId}`}
        >
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
};

const Stat = ({ label, value, testId }) => (
  <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5" data-testid={`stat-${testId}`}>
    <p className="text-xs uppercase tracking-[0.18em] text-zinc-500">{label}</p>
    <p className="mt-2 font-mono text-3xl font-semibold text-zinc-100">{value}</p>
  </div>
);

const Step = ({ n, title, children }) => (
  <div className="flex gap-4">
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-emerald-500/30 bg-emerald-500/10 font-mono text-sm text-emerald-300">
      {n}
    </div>
    <div className="flex-1 space-y-2">
      <h3 className="font-medium text-zinc-100">{title}</h3>
      <div className="text-sm text-zinc-400">{children}</div>
    </div>
  </div>
);

const Cmd = ({ name, desc }) => (
  <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3" data-testid={`cmd-${name}`}>
    <code className="font-mono text-sm text-emerald-300">/{name}</code>
    <p className="mt-1 text-xs text-zinc-400">{desc}</p>
  </div>
);

const Home = () => {
  const [stats, setStats] = useState({ total_uses: 0, total_messages_sent: 0, last_used: null });
  const [recent, setRecent] = useState([]);
  const [registering, setRegistering] = useState(false);
  const [installUrl, setInstallUrl] = useState("");
  const [botInstallUrl, setBotInstallUrl] = useState("");
  const [botStatus, setBotStatus] = useState({ is_running: false, alive: false });
  const [toggling, setToggling] = useState(false);

  const loadAll = async () => {
    try {
      const [s, r, i, b, bi] = await Promise.all([
        axios.get(`${API}/usage/stats`),
        axios.get(`${API}/usage/recent?limit=10`),
        axios.get(`${API}/discord/install-link`),
        axios.get(`${API}/bot/status`),
        axios.get(`${API}/discord/bot-install-link`),
      ]);
      setStats(s.data);
      setRecent(r.data);
      setInstallUrl(i.data.url);
      setBotStatus(b.data);
      setBotInstallUrl(bi.data.url);
    } catch (e) {
      console.error(e);
    }
  };

  const startBot = async () => {
    setToggling(true);
    try {
      const { data } = await axios.post(`${API}/bot/start`);
      setBotStatus((prev) => ({ ...prev, ...data }));
      toast.success("Bot is online (24/7).");
    } catch (e) {
      toast.error("Start failed: " + e.message);
    } finally {
      setToggling(false);
    }
  };

  const stopBot = async () => {
    setToggling(true);
    try {
      const { data } = await axios.post(`${API}/bot/stop`);
      setBotStatus((prev) => ({ ...prev, ...data }));
      toast("Bot stopped (kill switch).");
    } catch (e) {
      toast.error("Stop failed: " + e.message);
    } finally {
      setToggling(false);
    }
  };

  useEffect(() => {
    loadAll();
    const t = setInterval(loadAll, 5000);
    return () => clearInterval(t);
  }, []);

  const registerCmd = async () => {
    setRegistering(true);
    try {
      await axios.post(`${API}/discord/register-commands`);
      toast.success("Commands registered: /use, /blame, /raid");
    } catch (e) {
      toast.error("Registration failed: " + (e.response?.data?.detail || e.message));
    } finally {
      setRegistering(false);
    }
  };

  const lastUsed = stats.last_used ? new Date(stats.last_used).toLocaleString() : "—";
  const alive = botStatus.alive;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100" data-testid="home-page">
      <Toaster theme="dark" position="top-right" />

      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_30%_-10%,rgba(16,185,129,0.12),transparent_40%),radial-gradient(circle_at_80%_110%,rgba(99,102,241,0.10),transparent_40%)]" />

      <div className="relative mx-auto max-w-6xl px-6 py-12 sm:py-16">
        <header className="flex items-center justify-between" data-testid="header">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/10 ring-1 ring-emerald-500/30">
              <Zap className="h-5 w-5 text-emerald-400" />
            </div>
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.22em] text-zinc-500">
                discord · loop bot
              </p>
              <h1 className="font-mono text-xl font-semibold text-zinc-100">quintuple</h1>
            </div>
          </div>
          <Badge
            variant="outline"
            className={
              alive
                ? "border-emerald-500/30 bg-emerald-500/10 font-mono text-xs text-emerald-300"
                : "border-zinc-700 bg-zinc-900 font-mono text-xs text-zinc-400"
            }
            data-testid="status-badge"
          >
            {alive ? "● ONLINE" : "○ OFFLINE"}
          </Badge>
        </header>

        {/* Power control */}
        <section className="mt-10" data-testid="power-card">
          <Card
            className={
              "border bg-zinc-900/40 " + (alive ? "border-emerald-500/30" : "border-zinc-800")
            }
          >
            <CardContent className="flex flex-col items-start justify-between gap-6 p-6 sm:flex-row sm:items-center">
              <div className="flex items-center gap-4">
                <div
                  className={
                    "flex h-14 w-14 items-center justify-center rounded-full ring-1 " +
                    (alive
                      ? "bg-emerald-500/15 ring-emerald-500/40"
                      : "bg-zinc-800/60 ring-zinc-700")
                  }
                >
                  {alive ? (
                    <Power className="h-6 w-6 text-emerald-400" />
                  ) : (
                    <PowerOff className="h-6 w-6 text-zinc-500" />
                  )}
                </div>
                <div>
                  <p className="font-mono text-xs uppercase tracking-[0.18em] text-zinc-500">
                    bot power · 24/7
                  </p>
                  <p className="mt-1 text-lg text-zinc-100" data-testid="power-status-text">
                    {alive ? "Online — running 24/7" : "Offline — kill switch is active"}
                  </p>
                </div>
              </div>

              {alive ? (
                <Button
                  onClick={stopBot}
                  disabled={toggling}
                  className="bg-red-500 text-zinc-950 hover:bg-red-400"
                  data-testid="stop-bot-btn"
                >
                  <PowerOff className="mr-2 h-4 w-4" /> Stop Bot
                </Button>
              ) : (
                <Button
                  onClick={startBot}
                  disabled={toggling}
                  size="lg"
                  className="bg-emerald-500 text-zinc-950 hover:bg-emerald-400"
                  data-testid="start-bot-btn"
                >
                  <Power className="mr-2 h-5 w-5" /> Start Bot
                </Button>
              )}
            </CardContent>
          </Card>

          <p className="mt-3 flex items-start gap-2 text-xs text-zinc-500">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-400/80" />
            <span>
              Bot is online 24/7 by default. <b>Stop</b> is a manual kill switch.
            </span>
          </p>
        </section>

        {/* Hero */}
        <section className="mt-14 max-w-3xl" data-testid="hero">
          <h2 className="font-mono text-4xl font-semibold leading-tight text-zinc-100 sm:text-5xl lg:text-6xl">
            One slash. <span className="text-emerald-400">Many echoes.</span>
          </h2>
          <p className="mt-5 max-w-xl text-base text-zinc-400">
            User-installable Discord bot. Three commands, all powered by the same
            ephemeral-reply loop trick.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button
              onClick={registerCmd}
              disabled={registering}
              variant="outline"
              className="border-zinc-800 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
              data-testid="register-command-btn"
            >
              {registering ? (
                <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Send className="mr-2 h-4 w-4" />
              )}
              Register all commands
            </Button>
            {installUrl && (
              <a href={installUrl} target="_blank" rel="noreferrer" data-testid="install-link">
                <Button
                  variant="outline"
                  className="border-zinc-800 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
                >
                  <Link2 className="mr-2 h-4 w-4" /> Add to your account
                </Button>
              </a>
            )}
            {botInstallUrl && (
              <a href={botInstallUrl} target="_blank" rel="noreferrer" data-testid="bot-install-link">
                <Button
                  variant="outline"
                  className="border-amber-500/30 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20"
                >
                  <Link2 className="mr-2 h-4 w-4" /> Add bot to a server (for /raid)
                </Button>
              </a>
            )}
          </div>
        </section>

        {/* Commands */}
        <section className="mt-14 grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid="commands-section">
          <Cmd
            name="use message:hi"
            desc="Sends `hi` 5 times, 500ms apart. Replies look like 'Original deleted'."
          />
          <Cmd
            name="blame user:@x"
            desc="Ephemeral 'Blaming…' then a public 'Thank you @x for choosing loop bot ✅'."
          />
          <Cmd
            name="raid message:hi"
            desc="Sends 5x in every text channel the bot can access. Bot must be in the server."
          />
        </section>

        {/* Stats */}
        <section className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-3" data-testid="stats-section">
          <Stat label="Times used" value={stats.total_uses} testId="uses" />
          <Stat label="Messages sent" value={stats.total_messages_sent} testId="messages" />
          <Stat label="Last used" value={lastUsed} testId="last-used" />
        </section>

        {/* Setup + recent */}
        <section className="mt-14 grid grid-cols-1 gap-6 lg:grid-cols-5">
          <Card className="border-zinc-800 bg-zinc-900/40 lg:col-span-3" data-testid="setup-card">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-zinc-100">
                <BookOpen className="h-4 w-4 text-emerald-400" />
                Setup
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-8">
              <Step n="1" title="Set Interactions Endpoint URL">
                In{" "}
                <a
                  className="text-emerald-400 hover:underline"
                  href="https://discord.com/developers/applications"
                  target="_blank"
                  rel="noreferrer"
                >
                  Discord Developer Portal
                </a>{" "}
                → your app → <b>General Information</b>, paste this URL into{" "}
                <b>Interactions Endpoint URL</b> and save:
                <div className="mt-3">
                  <CopyField label="endpoint" value={INTERACTIONS_URL} testId="endpoint" />
                </div>
              </Step>
              <Step n="2" title="Enable User Install">
                In <b>Installation</b> tab, enable <b>User Install</b> under Installation Contexts.
              </Step>
              <Step n="3" title="Register commands">
                Click <b>Register all commands</b>. Registers <code>/use</code>, <code>/blame</code>,{" "}
                <code>/raid</code>.
              </Step>
              <Step n="4" title="Add bot to a server (for /raid only)">
                Click <b>Add bot to a server</b>. The bot needs to be a guild member with{" "}
                <b>Send Messages</b> permission so it can post in every channel.
              </Step>
            </CardContent>
          </Card>

          <Card className="border-zinc-800 bg-zinc-900/40 lg:col-span-2" data-testid="recent-card">
            <CardHeader>
              <CardTitle className="text-zinc-100">Recent activity</CardTitle>
            </CardHeader>
            <CardContent>
              {recent.length === 0 ? (
                <p className="text-sm text-zinc-500" data-testid="no-activity">
                  No usage yet.
                </p>
              ) : (
                <ul className="space-y-3" data-testid="recent-list">
                  {recent.map((r) => (
                    <li
                      key={r.id}
                      className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"
                      data-testid={`recent-item-${r.id}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-xs text-emerald-300">
                          @{r.username || "unknown"}
                        </span>
                        <span className="font-mono text-[10px] text-zinc-500">
                          {new Date(r.timestamp).toLocaleTimeString()}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-sm text-zinc-300">{r.message}</p>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </section>

        <footer className="mt-16 border-t border-zinc-900 pt-6 text-center font-mono text-xs text-zinc-600">
          quintuple · http interactions · 24/7
        </footer>
      </div>
    </div>
  );
};

function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
        </Routes>
      </BrowserRouter>
    </div>
  );
}

export default App;
