# EC2 Rightsizing Dashboard (Serverless AWS Project)

Daily EC2 rightsizing recommendations pulled from AWS Cost Explorer, stored as `latest.json` in S3, and rendered on a static HTML dashboard behind CloudFront.

## Architecture

- **EventBridge** – Triggers the Lambda function on a daily schedule
- **Lambda** – Calls Cost Explorer, generates rightsizing data, writes `latest.json` to S3
- **S3** – Stores both the static website files and the JSON data
- **CloudFront** – Fronts the S3 bucket and serves the dashboard globally

## Frontend

- `frontend/index.html` – Reads `latest.json` from S3 and displays recommendations in a table

## Lambda

- `lambda/rightsizing_lambda.py` – Python code that:
  - Queries Cost Explorer for EC2 rightsizing recommendations
  - Formats results (instance ID, current type, recommended type, savings, etc.)
  - Writes output to S3 as `latest.json`
