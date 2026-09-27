-- Attendly Database Schema
-- Run this after creating the database:
-- CREATE DATABASE attendly;
-- USE attendly;
-- SOURCE database/schema.sql;

-- ---------------------------------------------------
-- Table: employees
-- Stores both admin and normal employee accounts.
-- role = 'admin' or 'employee'
-- status = 'active' or 'inactive'
-- ---------------------------------------------------
CREATE TABLE IF NOT EXISTS employees (
    id INT AUTO_INCREMENT PRIMARY KEY,
    employee_id VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    department VARCHAR(50) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'employee',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------
-- Table: attendance
-- One row per employee per date.
-- status = 'Present' or 'Late'
-- (Absent is calculated in Python, not stored here)
-- ---------------------------------------------------
CREATE TABLE IF NOT EXISTS attendance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    employee_id INT NOT NULL,
    date DATE NOT NULL,
    login_time TIME DEFAULT NULL,
    logout_time TIME DEFAULT NULL,
    status VARCHAR(20) NOT NULL,
    UNIQUE KEY unique_attendance_per_day (employee_id, date),
    FOREIGN KEY (employee_id) REFERENCES employees(id)
);
