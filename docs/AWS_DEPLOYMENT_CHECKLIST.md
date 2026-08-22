# AWS Deployment Checklist

Follow this checklist strictly to deploy DBAutonomy to AWS while remaining within the Free Tier limits.

- [ ] AWS account verified
- [ ] Free Tier eligibility checked
- [ ] Region selected (e.g., us-east-1)
- [ ] EC2 created (`t2.micro` or `t3.micro`, 20GB EBS)
- [ ] Security group configured (Allow 22, 8501)
- [ ] SSH tested to EC2 instance
- [ ] Docker installed on EC2
- [ ] Repository cloned to EC2
- [ ] `.env` configured on EC2 (Set API Keys and external Ollama URL if hybrid)
- [ ] PostgreSQL configured (RDS initialized if used, or Docker Primary DB active)
- [ ] Redis running (`docker compose up -d redis`)
- [ ] FastAPI healthy (`docker compose up -d app`)
- [ ] Worker healthy (`docker compose up -d worker`)
- [ ] Dashboard accessible (http://<ec2-public-ip>:8501)
- [ ] Test query injected via Dashboard
- [ ] AI pipeline tested (Qwen -> Gemini -> LinUCB)
- [ ] Shadow benchmark works
- [ ] SafetyGate works (rejects malicious SQL)
- [ ] Deployment works (creates index successfully)
- [ ] AWS billing checked (Verify no unexpected hourly charges)
