# ibl.ai Infrastructure -- Multi-Server Setup
# App servers (public) + Services server (private) + optional managed DBs/cache

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.0"
}

provider "aws" {
  region = var.region
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  resource_prefix = "${var.project_name}-${var.environment}"

  # Use 2 or 3 AZs depending on region
  availability_zones = slice(
    data.aws_availability_zones.available.names,
    0,
    min(3, length(data.aws_availability_zones.available.names))
  )
  az_count = length(local.availability_zones)

  # S3 bucket naming
  domain_slug   = replace(var.base_domain, ".", "-")
  bucket_prefix = var.bucket_suffix != "" ? "${local.resource_prefix}-${local.domain_slug}-${var.bucket_suffix}" : "${local.resource_prefix}-${local.domain_slug}"

  # Certificate booleans
  use_acm    = var.certificate_method == "acm"
  use_upload = var.certificate_method == "upload"
  use_https  = local.use_acm || local.use_upload

  # Certificate 1: API and core services
  certificate_domains_1 = [
    "apps.learn.${var.base_domain}",
    "asgi.data.${var.base_domain}",
    "base.manager.${var.base_domain}",
    "learn.${var.base_domain}",
    "llm.data.${var.base_domain}",
    "mentor.data.${var.base_domain}",
    "preview.learn.${var.base_domain}",
    "web.data.${var.base_domain}",
  ]

  # Certificate 2: Auth and monitoring services
  certificate_domains_2 = [
    "studio.learn.${var.base_domain}",
    "status.${var.base_domain}",
    "mentorai.${var.base_domain}",
    "meilisearch.learn.${var.base_domain}",
    "monitor.${var.base_domain}",
    "flowise.${var.base_domain}",
    "skillsai.${var.base_domain}",
    "platform.${var.base_domain}",
    "prometheus.${var.base_domain}",
  ]

  all_certificate_domains = concat(local.certificate_domains_1, local.certificate_domains_2)
}

# ---------------------------------------------------------------------------
# VPC
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${local.resource_prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.resource_prefix}-igw" }
}

# ---------------------------------------------------------------------------
# Subnets
# ---------------------------------------------------------------------------

# Public subnets (app servers + ALB)
resource "aws_subnet" "public" {
  count                   = local.az_count
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone       = local.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.resource_prefix}-public-${count.index + 1}" }
}

# Private subnets (services server)
resource "aws_subnet" "private" {
  count             = local.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 11)
  availability_zone = local.availability_zones[count.index]

  tags = { Name = "${local.resource_prefix}-private-${count.index + 1}" }
}

# Database subnets (RDS)
resource "aws_subnet" "database" {
  count             = local.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 21)
  availability_zone = local.availability_zones[count.index]

  tags = { Name = "${local.resource_prefix}-db-${count.index + 1}" }
}

# Cache subnets (Redis)
resource "aws_subnet" "cache" {
  count             = local.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 31)
  availability_zone = local.availability_zones[count.index]

  tags = { Name = "${local.resource_prefix}-cache-${count.index + 1}" }
}

# ---------------------------------------------------------------------------
# NAT Gateways (private subnet internet access)
# ---------------------------------------------------------------------------

resource "aws_eip" "nat" {
  count  = local.az_count
  domain = "vpc"
  tags   = { Name = "${local.resource_prefix}-nat-eip-${count.index + 1}" }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count         = local.az_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "${local.resource_prefix}-nat-${count.index + 1}" }

  depends_on = [aws_internet_gateway.main]
}

# ---------------------------------------------------------------------------
# Route tables
# ---------------------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.resource_prefix}-public-rt" }
}

resource "aws_route_table" "private" {
  count  = local.az_count
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = { Name = "${local.resource_prefix}-private-rt-${count.index + 1}" }
}

resource "aws_route_table_association" "public" {
  count          = local.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = local.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ---------------------------------------------------------------------------
# Subnet groups (for managed services)
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  count      = var.enable_mysql || var.enable_postgres ? 1 : 0
  name       = "${local.resource_prefix}-db-subnet-group"
  subnet_ids = aws_subnet.database[*].id
  tags       = { Name = "${local.resource_prefix}-db-subnet-group" }
}

resource "aws_elasticache_subnet_group" "main" {
  count      = var.enable_redis ? 1 : 0
  name       = "${local.resource_prefix}-cache-subnet-group"
  subnet_ids = aws_subnet.cache[*].id
  tags       = { Name = "${local.resource_prefix}-cache-subnet-group" }
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.resource_prefix}-alb-sg"
  description = "ALB - HTTP/HTTPS from internet"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.resource_prefix}-alb-sg" }
}

resource "aws_security_group" "app_servers" {
  name        = "${local.resource_prefix}-app-sg"
  description = "App servers - SSH from VPN, HTTP from ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH from VPN"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["${var.vpn_ip}/32"]
  }

  ingress {
    description     = "HTTP from ALB"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.resource_prefix}-app-sg" }
}

resource "aws_security_group" "services" {
  name        = "${local.resource_prefix}-services-sg"
  description = "Services server - all TCP from app servers"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "All TCP from app servers"
    from_port       = 0
    to_port         = 65535
    protocol        = "tcp"
    security_groups = [aws_security_group.app_servers.id]
  }

  ingress {
    description = "SSH from VPN"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["${var.vpn_ip}/32"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.resource_prefix}-services-sg" }
}

resource "aws_security_group" "rds" {
  count       = var.enable_mysql || var.enable_postgres ? 1 : 0
  name        = "${local.resource_prefix}-rds-sg"
  description = "RDS - MySQL/PostgreSQL from app and services servers"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "MySQL from app servers"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.app_servers.id]
  }

  ingress {
    description     = "PostgreSQL from app servers"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app_servers.id]
  }

  ingress {
    description     = "MySQL from services server"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.services.id]
  }

  ingress {
    description     = "PostgreSQL from services server"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.services.id]
  }

  tags = { Name = "${local.resource_prefix}-rds-sg" }
}

resource "aws_security_group" "redis" {
  count       = var.enable_redis ? 1 : 0
  name        = "${local.resource_prefix}-redis-sg"
  description = "Redis - from app and services servers"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Redis from app servers"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app_servers.id]
  }

  ingress {
    description     = "Redis from services server"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.services.id]
  }

  tags = { Name = "${local.resource_prefix}-redis-sg" }
}

resource "aws_security_group" "efs" {
  name        = "${local.resource_prefix}-efs-sg"
  description = "EFS - NFS from app and services servers"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "NFS from app and services servers"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    security_groups = [
      aws_security_group.app_servers.id,
      aws_security_group.services.id,
    ]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.resource_prefix}-efs-sg" }
}

# ---------------------------------------------------------------------------
# SSH Key Pair
# ---------------------------------------------------------------------------

resource "aws_key_pair" "main" {
  count      = var.create_key_pair ? 1 : 0
  key_name   = "${local.resource_prefix}-key"
  public_key = var.ssh_public_key

  tags = { Name = "${local.resource_prefix}-key" }
}

# ---------------------------------------------------------------------------
# EC2 Instances
# ---------------------------------------------------------------------------

# App servers (public subnets, behind ALB)
resource "aws_instance" "app_servers" {
  count                  = var.app_server_count
  ami                    = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  instance_type          = var.app_server_instance_type
  key_name               = var.create_key_pair ? aws_key_pair.main[0].key_name : var.existing_key_pair_name
  vpc_security_group_ids = [aws_security_group.app_servers.id]
  subnet_id              = aws_subnet.public[count.index % local.az_count].id

  user_data = var.skip_user_data ? null : file("${path.module}/user_data_app.sh")

  root_block_device {
    volume_type = "gp3"
    volume_size = var.app_server_volume_size
    encrypted   = true
  }

  tags = { Name = "${local.resource_prefix}-app-${count.index + 1}" }
}

# Services server (private subnet)
resource "aws_instance" "services" {
  ami                    = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  instance_type          = var.services_instance_type
  key_name               = var.create_key_pair ? aws_key_pair.main[0].key_name : var.existing_key_pair_name
  vpc_security_group_ids = [aws_security_group.services.id]
  subnet_id              = aws_subnet.private[0].id

  user_data = var.skip_user_data ? null : file("${path.module}/user_data_services.sh")

  root_block_device {
    volume_type = "gp3"
    volume_size = var.services_volume_size
    encrypted   = true
  }

  tags = { Name = "${local.resource_prefix}-services" }
}

# ---------------------------------------------------------------------------
# EFS (shared storage across app servers)
# ---------------------------------------------------------------------------

resource "aws_efs_file_system" "media" {
  creation_token = "${local.resource_prefix}-media"
  encrypted      = true

  tags = { Name = "${local.resource_prefix}-media" }
}

resource "aws_efs_mount_target" "media" {
  count           = length(aws_subnet.public)
  file_system_id  = aws_efs_file_system.media.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# ---------------------------------------------------------------------------
# S3 Buckets
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "backups" {
  bucket = "${local.bucket_prefix}-backups"
  tags   = { Name = "${local.bucket_prefix}-backups" }
}

resource "aws_s3_bucket" "dm_media" {
  bucket = "${local.bucket_prefix}-dm-media"
  tags   = { Name = "${local.bucket_prefix}-dm-media" }
}

resource "aws_s3_bucket" "dm_static" {
  bucket = "${local.bucket_prefix}-dm-static"
  tags   = { Name = "${local.bucket_prefix}-dm-static" }
}

resource "aws_s3_bucket_public_access_block" "dm_static" {
  bucket = aws_s3_bucket.dm_static.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "dm_static" {
  bucket = aws_s3_bucket.dm_static.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicReadGetObject"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.dm_static.arn}/*"
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.dm_static]
}

# ---------------------------------------------------------------------------
# Application Load Balancer
# ---------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = "${local.resource_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = false
  enable_http2               = true

  tags = { Name = "${local.resource_prefix}-alb" }
}

resource "aws_lb_target_group" "app" {
  name     = "${local.resource_prefix}-app-tg"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
    path                = "/"
    matcher             = "200"
  }

  tags = { Name = "${local.resource_prefix}-app-tg" }
}

resource "aws_lb_target_group_attachment" "app" {
  count            = var.app_server_count
  target_group_arn = aws_lb_target_group.app.arn
  target_id        = aws_instance.app_servers[count.index].id
  port             = 80
}

# ---------------------------------------------------------------------------
# HTTP Listener
# ---------------------------------------------------------------------------

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = local.use_https ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  dynamic "default_action" {
    for_each = local.use_https ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.app.arn
    }
  }
}

# ---------------------------------------------------------------------------
# ACM Certificates (conditional on certificate_method == "acm")
# ---------------------------------------------------------------------------

resource "aws_acm_certificate" "main_1" {
  count = local.use_acm ? 1 : 0

  domain_name               = "api.data.${var.base_domain}"
  subject_alternative_names = local.certificate_domains_1
  validation_method         = "DNS"

  lifecycle { create_before_destroy = true }
  tags = { Name = "${local.resource_prefix}-cert-1" }
}

resource "aws_acm_certificate" "main_2" {
  count = local.use_acm ? 1 : 0

  domain_name               = "auth.${var.base_domain}"
  subject_alternative_names = local.certificate_domains_2
  validation_method         = "DNS"

  lifecycle { create_before_destroy = true }
  tags = { Name = "${local.resource_prefix}-cert-2" }
}

# Route53 validation records
data "aws_route53_zone" "main" {
  count   = local.use_acm ? 1 : 0
  zone_id = var.hosted_zone_id
}

resource "aws_route53_record" "cert_validation_1" {
  for_each = local.use_acm ? {
    for dvo in aws_acm_certificate.main_1[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60
}

resource "aws_route53_record" "cert_validation_2" {
  for_each = local.use_acm ? {
    for dvo in aws_acm_certificate.main_2[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60
}

resource "aws_acm_certificate_validation" "main_1" {
  count                   = local.use_acm ? 1 : 0
  certificate_arn         = aws_acm_certificate.main_1[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation_1 : r.fqdn]
  depends_on              = [aws_route53_record.cert_validation_1]
  timeouts { create = "10m" }
}

resource "aws_acm_certificate_validation" "main_2" {
  count                   = local.use_acm ? 1 : 0
  certificate_arn         = aws_acm_certificate.main_2[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation_2 : r.fqdn]
  depends_on              = [aws_route53_record.cert_validation_2]
  timeouts { create = "10m" }
}

# Route53 A records (alias to ALB)
resource "aws_route53_record" "app" {
  for_each = local.use_acm ? toset(concat(
    ["api.data.${var.base_domain}", "auth.${var.base_domain}"],
    local.all_certificate_domains
  )) : toset([])

  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = each.value
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

# ---------------------------------------------------------------------------
# Uploaded Certificates (conditional on certificate_method == "upload")
# ---------------------------------------------------------------------------

resource "aws_iam_server_certificate" "uploaded" {
  count = local.use_upload ? 1 : 0

  name_prefix       = "${local.resource_prefix}-cert-"
  certificate_body  = file("${path.module}/${var.certificate_body_file}")
  private_key       = file("${path.module}/${var.certificate_key_file}")
  certificate_chain = var.certificate_chain_file != "" ? file("${path.module}/${var.certificate_chain_file}") : null

  lifecycle { create_before_destroy = true }
}

# ---------------------------------------------------------------------------
# HTTPS Listener (conditional on any cert method)
# ---------------------------------------------------------------------------

resource "aws_lb_listener" "https" {
  count = local.use_https ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS-1-2-2017-01"
  certificate_arn   = local.use_acm ? aws_acm_certificate_validation.main_1[0].certificate_arn : aws_iam_server_certificate.uploaded[0].arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_lb_listener_certificate" "additional" {
  count = local.use_acm ? 1 : 0

  listener_arn    = aws_lb_listener.https[0].arn
  certificate_arn = aws_acm_certificate_validation.main_2[0].certificate_arn
}

# ---------------------------------------------------------------------------
# MySQL RDS (optional)
# ---------------------------------------------------------------------------

resource "aws_db_instance" "mysql" {
  count = var.enable_mysql ? 1 : 0

  identifier     = "${local.resource_prefix}-mysql"
  engine         = "mysql"
  engine_version = "8.4"
  instance_class = var.rds_mysql_instance_class

  allocated_storage     = var.rds_mysql_storage_size
  max_allocated_storage = 1000
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.mysql_database_name
  username = var.mysql_username
  password = var.mysql_password

  vpc_security_group_ids = [aws_security_group.rds[0].id]
  db_subnet_group_name   = aws_db_subnet_group.main[0].name

  backup_retention_period   = 7
  backup_window             = "03:00-04:00"
  maintenance_window        = "sun:04:00-sun:05:00"
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.resource_prefix}-mysql-final"
  deletion_protection       = true
  multi_az                  = true

  tags = { Name = "${local.resource_prefix}-mysql" }
}

# ---------------------------------------------------------------------------
# PostgreSQL RDS (optional)
# ---------------------------------------------------------------------------

resource "aws_db_instance" "postgres" {
  count = var.enable_postgres ? 1 : 0

  identifier     = "${local.resource_prefix}-postgres"
  engine         = "postgres"
  engine_version = "15"
  instance_class = var.rds_postgres_instance_class

  allocated_storage     = var.rds_postgres_storage_size
  max_allocated_storage = 1000
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.postgres_database_name
  username = var.postgres_username
  password = var.postgres_password

  vpc_security_group_ids = [aws_security_group.rds[0].id]
  db_subnet_group_name   = aws_db_subnet_group.main[0].name

  backup_retention_period   = 7
  backup_window             = "03:00-04:00"
  maintenance_window        = "sun:04:00-sun:05:00"
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.resource_prefix}-postgres-final"
  deletion_protection       = true
  multi_az                  = true

  tags = { Name = "${local.resource_prefix}-postgres" }
}

# ---------------------------------------------------------------------------
# Redis ElastiCache (optional)
# ---------------------------------------------------------------------------

resource "aws_elasticache_replication_group" "redis" {
  count                = var.enable_redis ? 1 : 0
  replication_group_id = "${local.resource_prefix}-redis"
  description          = "Redis cluster for ${local.resource_prefix}"

  node_type                  = var.redis_instance_type
  port                       = 6379
  parameter_group_name       = "default.redis7"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true

  subnet_group_name  = aws_elasticache_subnet_group.main[0].name
  security_group_ids = [aws_security_group.redis[0].id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = var.redis_auth_token != "" ? var.redis_auth_token : null

  snapshot_retention_limit = 7
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "sun:05:00-sun:07:00"

  tags = { Name = "${local.resource_prefix}-redis" }
}
