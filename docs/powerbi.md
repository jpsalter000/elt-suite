# Power BI

`powerbi/Utilization.pbip` is a Power BI Project: a text-based semantic model (TMDL) and report (PBIR) that open in Power BI Desktop. The model is **generated** from the dbt reporting contracts, and the report pages mirror the original workbook.

## Contents

- **Tables:** imported from the `reporting` schema.
  - Dimensions: `Date` (marked as the date table), `Employee`, `Client`, `Project` and `Item`.
  - Facts: `Time Entries` and `Employee Capacity`.
  - `Project Margin`.
- **Relationships:** single-direction, many-to-one, from the facts to the dimensions.
- **Measures:** in a `Measures` table, in three display folders:

  | Folder | Measures |
  | --- | --- |
  | Hours | Total, Worked, Client, Exempt, Expected and Standard Hours |
  | Utilization | Net Utilization %, Project Work %, Client BD %, Internal % and Exempt % |
  | Clients and projects | Client Rank, Price, Net Revenue, Project Profit, Profit %, Contribution Margin and CM % |

  The definitions are in [utilization-logic.md](utilization-logic.md).
- **Pages:**
  - Main Report
  - Time to Clients
  - Breakdown - Tasks by Person
  - Breakdown - Persons by Task
  - Project Contribution Margin

Per-person cost rates never reach Power BI: the `reporting` schema exposes costs only rolled up to projects.

## Connecting

The model has two parameters, `Server` (default `localhost:5432`) and `Database` (default `warehouse`). It connects with the `powerbi` login, which dbt creates. That login only inherits `reporting_reader`, so it can read the `reporting` schema and nothing else.

Set its password once as an administrator. dbt never sets it, so it can't leak into dbt's logs.

```bash
# local docker compose
docker compose exec warehouse psql -U elt -d warehouse -c "ALTER ROLE powerbi PASSWORD 'choose-one'"
```

**Locally:**
1. `docker compose up -d`.
2. Run the pipeline: `elt pipeline run utilization_daily`, with the NetSuite mock running (`elt-mock netsuite`).
3. Open `powerbi/Utilization.pbip`.
4. Sign in to the PostgreSQL source as `powerbi`, then refresh.

**AWS (RDS):** the database sits in isolated subnets, so reach it through an SSM port-forward on the Airflow task:
1. Start the port-forward (below).
2. Point `Server` at the RDS hostname. With `rds.force_ssl` on, the TLS certificate must match the hostname.
3. Map the RDS hostname to `127.0.0.1` in your hosts file.
4. Install the [RDS CA bundle](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html) in the Windows certificate store.

```bash
aws ssm start-session --target "ecs:${cluster}_${task_id}_${runtime_id}" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"$RDS_HOST\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"5432\"]}"
```

(See [orchestration.md](orchestration.md) for finding the task and runtime ids.) Scheduled refresh in the Power BI Service would need an on-premises data gateway inside the VPC; that is out of scope.

## Changing the model

Don't edit `Utilization.SemanticModel` or `Utilization.Report` by hand. Change the dbt contract (`transform/models/reporting/_reporting.yml`) or `powerbi/generate.py`, then:

```bash
uv run python powerbi/generate.py
uv run pytest tests/test_powerbi_model.py
```

The test fails if:
- the committed files differ from the generator's output;
- any table reads a view or column outside the dbt contracts;
- any DAX reference, relationship or visual field doesn't resolve;
- any project file breaks its published JSON schema. The schemas are vendored in `powerbi/schemas/`; refresh them with `uv run python powerbi/vendor_schemas.py`.

Power BI Desktop stays the final check for visuals: open the project after changing pages.
