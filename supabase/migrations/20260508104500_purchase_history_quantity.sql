alter table purchase_history
add column if not exists quantity numeric(8,2) not null default 1;
