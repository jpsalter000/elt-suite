-- Categorize each time entry, split its hours into category buckets and cost it.
-- Replaces the workbook's "Is <category>" and "<category> Hours" calculated columns.
{%- set buckets = ['project_work', 'client_bd', 'internal_productive', 'internal_admin', 'exempt'] %}

with entries as (
    select * from {{ ref('stg_netsuite__time_entries') }}
),

items as (
    select * from {{ ref('stg_netsuite__items') }}
),

categories as (
    select * from {{ ref('time_categories') }}
),

projects as (
    select * from {{ ref('stg_netsuite__projects') }}
),

employees as (
    select * from {{ ref('stg_netsuite__employees') }}
)

select
    entries.time_entry_id,
    entries.entry_date,
    entries.employee_id,
    entries.department_id,
    entries.item_id,
    items.item_name,
    coalesce(categories.category_code, 'unmapped') as category_code,
    coalesce(categories.category_name, 'Unmapped') as category_name,
    coalesce(categories.counts_as_worked, false) as counts_as_worked,
    coalesce(categories.is_client_facing, false) as is_client_facing,
    projects.project_id,
    -- A project's client is its customer; otherwise the entry names the client directly.
    coalesce(
        projects.customer_id,
        case when projects.project_id is null then entries.customer_ref_id end
    ) as client_id,
    entries.hours,
    (case when categories.counts_as_worked then entries.hours else 0 end)::numeric(8, 2)
        as worked_hours,
    (case when categories.is_client_facing then entries.hours else 0 end)::numeric(8, 2)
        as client_facing_hours,
    {%- for bucket in buckets %}
    (case when categories.category_code = '{{ bucket }}' then entries.hours else 0 end)
        ::numeric(8, 2) as {{ bucket }}_hours,
    {%- endfor %}
    (case when categories.category_code is null then entries.hours else 0 end)::numeric(8, 2)
        as unmapped_hours,
    (entries.hours * employees.cost_per_hour)::numeric(14, 4) as time_cost,
    (entries.hours * employees.burdened_cost_per_hour)::numeric(14, 4) as burdened_time_cost,
    entries.is_billable,
    entries.memo
from entries
left join items on entries.item_id = items.item_id
left join categories on lower(categories.item_name) = lower(items.item_name)
left join projects on entries.customer_ref_id = projects.project_id
left join employees on entries.employee_id = employees.employee_id
