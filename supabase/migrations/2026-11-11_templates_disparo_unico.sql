-- Templates de disparo único: apagados da Meta quando a campanha real termina.
-- Aplicar no SQL Editor do projeto (idempotente: pode rodar mais de uma vez).

create table if not exists single_use_templates (
  name                text        not null,
  language            text        not null,
  template_id         text,                     -- hsm_id: apaga só este idioma
  created_at          timestamptz not null default now(),
  deleted_at          timestamptz,
  deleted_by_campaign text        references campaigns(id) on delete set null,
  delete_error        text,
  primary key (name, language)
);

alter table single_use_templates enable row level security;

-- Marcada só na campanha real (nem teste, nem simulação) de um template de disparo único.
alter table campaigns add column if not exists delete_template_on_finish boolean not null default false;

-- A view expande c.* na criação: sem recriá-la, a coluna nova não aparece nela.
-- "create or replace" em vez de drop (a view nunca deixa de existir), o que exige manter
-- as colunas antigas na mesma ordem e só acrescentar no fim. heartbeat_at entra junto:
-- foi criada depois da view e também não aparecia nela.
create or replace view campaign_overview as
select
  c.id, c.label, c.template, c.template_full, c.header_media, c.dry_run,
  c.rate, c.rate_real, c.interval_s, c.time_budget_s, c.workers, c.freios,
  c.status, c.stop_reason, c.total, c.sent, c.failed, c.processed,
  c.parent_id, c.resumed_by, c.created_at, c.started_at, c.finished_at, c.interrupted_at,
  (select count(*) from recipients r
    where r.campaign_id = c.id and r.status = 'pending') as pending,
  case
    when c.started_at is null then null
    else extract(epoch from (coalesce(c.finished_at, c.interrupted_at, now()) - c.started_at))::int
  end as duracao_s,
  c.heartbeat_at,
  c.delete_template_on_finish
from campaigns c;

alter view campaign_overview set (security_invoker = on);
