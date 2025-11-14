# aws-ec2-rightsizing-dashboar
A fully serverless cost-optimization tool built on AWS. A scheduled Lambda function uses Cost Explorer to generate daily EC2 rightsizing recommendations, stores them as latest.json in S3, and serves a real-time dashboard through CloudFront. Includes automated savings calculations, sortable tables, and continuous daily updates.
