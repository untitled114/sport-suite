variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
}

variable "instance_type" {
  description = "EC2 instance type. t3.xlarge (4 vCPU / 16GB)."
  type        = string
  default     = "t3.xlarge"
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 50
}

variable "data_volume_size_gb" {
  description = "Data EBS volume size in GB (Postgres data + ML models)"
  type        = number
  default     = 200
}

variable "ssh_public_key_path" {
  description = "Path to SSH public key for EC2 access"
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "allowed_ssh_cidrs" {
  description = "CIDR blocks allowed to SSH. Required: set your own address range(s), for example [\"203.0.113.10/32\"]."
  type        = list(string)

  validation {
    condition     = length(var.allowed_ssh_cidrs) > 0
    error_message = "Set allowed_ssh_cidrs to at least one address range."
  }

  validation {
    condition     = !contains(var.allowed_ssh_cidrs, "0.0.0.0/0")
    error_message = "allowed_ssh_cidrs must not contain 0.0.0.0/0: SSH is never open to every address."
  }
}

variable "airflow_ui_cidrs" {
  description = "IPv4 CIDR blocks allowed to access Airflow UI (port 8080). Use SSH tunnel instead for security."
  type        = list(string)
  default     = []
}

variable "airflow_ui_ipv6_cidrs" {
  description = "IPv6 CIDR blocks allowed to access Airflow UI (port 8080)."
  type        = list(string)
  default     = []
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  description = "Public subnet CIDR"
  type        = string
  default     = "10.0.1.0/24"
}

variable "availability_zone" {
  description = "Availability zone for subnet and EBS volumes"
  type        = string
  default     = "us-east-1a"
}

variable "backup_retention_days" {
  description = "Days to retain S3 backups before expiring"
  type        = number
  default     = 30
}
