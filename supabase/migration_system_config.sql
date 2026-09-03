create table if not exists system_config (
    key text primary key,
    value jsonb not null,
    updated_at timestamptz not null default now()
);

insert into system_config (key, value) values ('automation_enabled', 'true'::jsonb)
on conflict (key) do nothing;

NOTIFY pgrst, 'reload schema';
