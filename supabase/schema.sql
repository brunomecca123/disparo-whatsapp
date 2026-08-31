-- DisparoMais — esquema para migração do estado em disco/memória para o Supabase.
-- Aplicar no SQL Editor do projeto. Os ids são texto (hex de 12) para manter
-- compatibilidade com o histórico já gravado em campaigns/*.json.

-- ---------------------------------------------------------------- campanhas
create table if not exists campaigns (
  id            text primary key,
  label         text        not null,
  template      jsonb       not null,          -- {name, language}
  template_full jsonb       not null,          -- template completo da Meta
  header_media  jsonb,
  dry_run       boolean     not null default false,

  -- ritmo
  rate          numeric,                        -- mensagens por segundo (alvo)
  rate_real     numeric,                        -- média efetiva da última execução
  interval_s    numeric,                        -- formato antigo, mantido para histórico
  time_budget_s numeric,                        -- teto de tempo por execução (Vercel: ~270)
  workers       int,
  freios        int         not null default 0, -- quantas vezes a Meta pediu para desacelerar

  -- situação
  status        text        not null default 'pending'
                check (status in ('pending','running','finished','cancelled','interrupted','paused')),
  stop_reason   text        check (stop_reason in ('usuario','tempo','processo')),

  total         int         not null default 0,
  sent          int         not null default 0,
  failed        int         not null default 0,
  processed     int         not null default 0,

  parent_id     text        references campaigns(id) on delete set null,
  resumed_by    text,

  created_at     timestamptz not null default now(),
  started_at     timestamptz,
  finished_at    timestamptz,
  interrupted_at timestamptz
);

create index if not exists campaigns_created_idx on campaigns (created_at desc);
create index if not exists campaigns_status_idx  on campaigns (status);

-- ------------------------------------------------------------ destinatários
-- "values" é palavra reservada no Postgres: a coluna com as variáveis chama-se vars.
create table if not exists recipients (
  id          bigserial primary key,
  campaign_id text    not null references campaigns(id) on delete cascade,
  idx         int     not null,                -- ordem original da lista
  phone       text    not null,
  original    text,
  vars        jsonb   not null default '{}',
  status      text    not null default 'pending'
              check (status in ('pending','sent','failed','dry_run')),
  message_id  text,
  error       text,
  sent_at     timestamptz
);

-- O worker busca sempre "os próximos pendentes desta campanha, em ordem".
create index if not exists recipients_fila_idx on recipients (campaign_id, status, idx);
create unique index if not exists recipients_unicos_idx on recipients (campaign_id, idx);

-- ------------------------------------------------------------------ uploads
-- Substitui o dicionário _uploads em memória: as linhas do CSV/Excel ficam aqui
-- entre o upload e o disparo, que na Vercel são invocações diferentes.
create table if not exists uploads (
  id         text primary key,
  source     text,                              -- nome do arquivo ou "lista colada"
  columns    jsonb not null default '[]',
  rows       jsonb not null default '[]',
  row_count  int   not null default 0,
  created_at timestamptz not null default now()
);

create index if not exists uploads_created_idx on uploads (created_at desc);

-- --------------------------------------------------------------- blocklist
-- Substitui blocklist.txt (opt-out).
create table if not exists blocklist (
  phone      text primary key,
  motivo     text,
  created_at timestamptz not null default now()
);

-- ------------------------------------------------------------------- visão
-- Pendentes contados na hora: é o número que decide se dá para retomar.
create or replace view campaign_overview as
select
  c.*,
  (select count(*) from recipients r
    where r.campaign_id = c.id and r.status = 'pending') as pending,
  case
    when c.started_at is null then null
    else extract(epoch from (coalesce(c.finished_at, c.interrupted_at, now()) - c.started_at))::int
  end as duracao_s
from campaigns c;

-- Sem isto a view roda como SECURITY DEFINER (dona: postgres) e devolveria as
-- campanhas para a anon key, furando o RLS das tabelas abaixo.
alter view campaign_overview set (security_invoker = on);

-- --------------------------------------------------------------- segurança
-- O aplicativo acessa com a service_role key, que ignora RLS. Ligamos RLS sem
-- criar policies: assim a anon key (pública) não lê nem escreve nada.
alter table campaigns  enable row level security;
alter table recipients enable row level security;
alter table uploads    enable row level security;
alter table blocklist  enable row level security;
