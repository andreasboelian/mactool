-- Tabellen für den Fernzugriff auf die Macs.
--
-- Einmalig im SQL-Editor der Dashboard-Supabase ausführen (dasselbe Projekt, das
-- in config.json unter `supabase_url` steht). Danach genügt auf jedem Mac ein
-- Update auf v1.0.115 — die Macs melden sich von allein.
--
-- Mehrfach ausführbar: alles ist "if not exists".

-- ── Der Briefkasten ───────────────────────────────────────────────────
-- Wir legen eine Zeile ab, der Mac holt sie sich und schreibt das Ergebnis
-- in dieselbe Zeile zurück. Jeder Befehl bleibt damit nachvollziehbar stehen.
create table if not exists public.mac_commands (
  id           bigserial primary key,
  server_name  text        not null,           -- an welchen Mac (= config.server_name)
  command      text        not null,           -- siehe remote_commands.HANDLERS
  args         jsonb       not null default '{}'::jsonb,
  status       text        not null default 'queued',
                                               -- queued | running | done | error | expired
  requested_by text,                           -- wer hat ihn abgeschickt
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null default now() + interval '15 minutes',
  claimed_at   timestamptz,
  finished_at  timestamptz,
  result       jsonb,
  error        text
);

-- Die einzige Abfrage, die alle 15 Sekunden von jedem Mac läuft
create index if not exists mac_commands_pending_idx
  on public.mac_commands (server_name, status, created_at);

-- ── Wer lebt überhaupt ────────────────────────────────────────────────
-- Der Mac trägt sich hier einmal pro Minute ein. Ein Mac, der fehlt oder
-- dessen last_seen alt ist, ist aus — das sieht man ohne einen Befehl.
create table if not exists public.mac_agents (
  server_name      text primary key,
  last_seen        timestamptz not null default now(),
  agent_clock      timestamptz,   -- was die Uhr des Macs selbst sagt
  version          text,
  hostname         text,
  platform         text,
  bot_running      boolean,
  rustdesk_running boolean,
  actions_allowed  boolean,
  last_runs        jsonb
);

-- Nachtrag fuer Installationen, die die erste Fassung schon ausgefuehrt haben
alter table public.mac_agents add column if not exists agent_clock timestamptz;

-- last_seen kommt von der Datenbank, nicht vom Mac. Auf mac07 ging die Uhr
-- sieben Minuten nach — jeder Heartbeat sah dadurch abgelaufen aus, obwohl der
-- Mac gerade eben geschrieben hatte. Die Uhr der Datenbank ist die eine
-- Referenz, die alle teilen; die Mac-Uhr steht daneben in agent_clock und macht
-- eine Abweichung damit sichtbar statt verwirrend.
create or replace function public.mac_agents_stamp_last_seen()
returns trigger language plpgsql as $$
begin
  new.last_seen := now();
  return new;
end;
$$;

drop trigger if exists mac_agents_stamp on public.mac_agents;
create trigger mac_agents_stamp
  before insert or update on public.mac_agents
  for each row execute function public.mac_agents_stamp_last_seen();

-- ── Zugriff ───────────────────────────────────────────────────────────
-- RLS an, aber bewusst *ohne* Policy: der service_role-Key (den die Macs und
-- tools/macctl.py benutzen) umgeht RLS, jede andere Rolle kommt damit an
-- keine der beiden Tabellen heran. Insbesondere anon und authenticated.
alter table public.mac_commands enable row level security;
alter table public.mac_agents   enable row level security;

-- ── Aufräumen ─────────────────────────────────────────────────────────
-- Erledigte Befehle nach 30 Tagen wegwerfen. Ohne pg_cron gelegentlich von Hand
-- ausführen; die Tabelle wächst langsam genug, dass das reicht.
-- delete from public.mac_commands
--  where status in ('done','error','expired') and created_at < now() - interval '30 days';
