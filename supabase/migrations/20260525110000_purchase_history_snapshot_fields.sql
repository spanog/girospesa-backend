alter table purchase_history
add column if not exists brand text,
add column if not exists format_label text,
add column if not exists image_url text,
add column if not exists category text,
add column if not exists subcategory text,
add column if not exists unit_price text,
add column if not exists unit_price_value numeric(10,2),
add column if not exists unit_price_unit text,
add column if not exists unit_price_label text;
