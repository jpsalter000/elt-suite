-- Project Contribution Margin.
--   price         invoices and credit memos for finalized or closed projects,
--                 otherwise sales orders (including pass-through lines)
--   net revenue   price minus pass-through travel and incentive revenue
--   profit        price minus field, incentive and travel costs and time cost
--   CM            the same with the burdened ("CB") rate
-- The workbook counted pass-through revenue inside its Travel and Incentive columns
-- and then subtracted those columns from price, removing pass-through twice. Here
-- pass-through revenue and the costs it reimburses are kept apart.

with financials as (
    select
        project_id,
        sum(price_amount) as price,
        sum(pass_through_amount) as pass_through_revenue,
        sum(field_cost) as field_cost,
        sum(incentive_cost) as incentive_cost,
        sum(travel_cost) as travel_cost
    from {{ ref('fct_project_financials') }}
    group by 1
),

time_spent as (
    select
        project_id,
        sum(hours) as total_hours,
        sum(time_cost) as time_cost,
        sum(burdened_time_cost) as burdened_time_cost
    from {{ ref('fct_time_entries') }}
    where project_id is not null
    group by 1
),

projects as (
    select
        projects.*,
        coalesce(time_spent.total_hours, 0) as total_hours,
        coalesce(time_spent.time_cost, 0) as time_cost,
        coalesce(time_spent.burdened_time_cost, 0) as burdened_time_cost,
        coalesce(financials.price, 0) as price,
        coalesce(financials.pass_through_revenue, 0) as pass_through_revenue,
        coalesce(financials.field_cost, 0) as field_cost,
        coalesce(financials.incentive_cost, 0) as incentive_cost,
        coalesce(financials.travel_cost, 0) as travel_cost
    from {{ ref('dim_project') }} as projects
    left join financials on projects.project_id = financials.project_id
    left join time_spent on projects.project_id = time_spent.project_id
),

margins as (
    select
        *,
        price - pass_through_revenue as net_revenue,
        price - (field_cost + incentive_cost + travel_cost + time_cost) as project_profit,
        price - (field_cost + incentive_cost + travel_cost + burdened_time_cost)
            as contribution_margin
    from projects
)

select
    project_id::integer as project_id,
    project_display_name::text as project_display_name,
    client_name::text as client_name,
    department_name::text as department_name,
    project_lead::text as project_lead,
    project_status_label::text as project_status_label,
    start_date::date as start_date,
    total_hours::numeric(10, 2) as total_hours,
    time_cost::numeric(14, 2) as time_cost,
    burdened_time_cost::numeric(14, 2) as burdened_time_cost,
    price::numeric(14, 2) as price,
    pass_through_revenue::numeric(14, 2) as pass_through_revenue,
    net_revenue::numeric(14, 2) as net_revenue,
    field_cost::numeric(14, 2) as field_cost,
    incentive_cost::numeric(14, 2) as incentive_cost,
    travel_cost::numeric(14, 2) as travel_cost,
    project_profit::numeric(14, 2) as project_profit,
    round(project_profit / nullif(net_revenue, 0), 4)::numeric(9, 4) as project_profit_pct,
    contribution_margin::numeric(14, 2) as contribution_margin,
    round(contribution_margin / nullif(net_revenue, 0), 4)::numeric(9, 4)
        as contribution_margin_pct
from margins
