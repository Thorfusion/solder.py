-- SANITIZED TEST FIXTURE: contains no source data or credentials.
-- Schema provenance: TechnicPack/TechnicSolder v1.3.1 (262d2ae4693b8ce73b0e122d8f2c3a447fd57afe).
-- Generated from the official migrations against MySQL 8.4.

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `build_modversion` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `modversion_id` int NOT NULL,
  `build_id` int NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `build_modversion_modversion_id_index` (`modversion_id`),
  KEY `build_modversion_build_id_index` (`build_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `builds` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `modpack_id` int NOT NULL,
  `version` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `minecraft` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
  `forge` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `is_published` tinyint(1) NOT NULL DEFAULT '0',
  `private` tinyint(1) NOT NULL DEFAULT '0',
  `min_java` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `min_memory` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `builds_modpack_id_index` (`modpack_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `client_modpack` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `client_id` int NOT NULL,
  `modpack_id` int NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `client_modpack_client_id_index` (`client_id`),
  KEY `client_modpack_modpack_id_index` (`modpack_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `clients` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `uuid` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `clients_uuid_index` (`uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `keys` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `api_key` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `keys_api_key_index` (`api_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `migrations` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `migration` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `batch` int NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `modpacks` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `slug` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `recommended` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `latest` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `url` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `icon_md5` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `logo_md5` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `background_md5` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `order` int NOT NULL DEFAULT '0',
  `hidden` tinyint(1) NOT NULL DEFAULT '1',
  `private` tinyint(1) NOT NULL DEFAULT '0',
  `icon` tinyint(1) NOT NULL DEFAULT '0',
  `logo` tinyint(1) NOT NULL DEFAULT '0',
  `background` tinyint(1) NOT NULL DEFAULT '0',
  `icon_url` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `logo_url` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `background_url` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `modpacks_name_unique` (`name`),
  UNIQUE KEY `modpacks_slug_unique` (`slug`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mods` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `author` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `link` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `notes` text COLLATE utf8mb4_unicode_ci,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `pretty_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `mods_name_unique` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `modversions` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `mod_id` int NOT NULL,
  `version` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `md5` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `filesize` int DEFAULT NULL,
  `notes` text COLLATE utf8mb4_unicode_ci,
  PRIMARY KEY (`id`),
  KEY `modversions_mod_id_index` (`mod_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `password_reset_tokens` (
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `token` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `personal_access_tokens` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `tokenable_type` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `tokenable_id` bigint unsigned NOT NULL,
  `name` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `token` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `abilities` text COLLATE utf8mb4_unicode_ci,
  `last_used_at` timestamp NULL DEFAULT NULL,
  `expires_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `personal_access_tokens_token_unique` (`token`),
  KEY `personal_access_tokens_tokenable_type_tokenable_id_index` (`tokenable_type`,`tokenable_id`),
  KEY `personal_access_tokens_expires_at_index` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_permissions` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `solder_full` tinyint(1) NOT NULL DEFAULT '0',
  `solder_users` tinyint(1) NOT NULL DEFAULT '0',
  `mods_create` tinyint(1) NOT NULL DEFAULT '0',
  `mods_manage` tinyint(1) NOT NULL DEFAULT '0',
  `mods_delete` tinyint(1) NOT NULL DEFAULT '0',
  `modpacks` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `solder_keys` tinyint(1) NOT NULL DEFAULT '0',
  `solder_clients` tinyint(1) NOT NULL DEFAULT '0',
  `modpacks_create` tinyint(1) NOT NULL DEFAULT '0',
  `modpacks_manage` tinyint(1) NOT NULL DEFAULT '0',
  `modpacks_delete` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `user_permissions_user_id_index` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `username` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `two_factor_secret` text COLLATE utf8mb4_unicode_ci,
  `two_factor_recovery_codes` text COLLATE utf8mb4_unicode_ci,
  `two_factor_confirmed_at` timestamp NULL DEFAULT NULL,
  `created_ip` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_ip` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `remember_token` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
  `updated_by_ip` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_by_user_id` int NOT NULL DEFAULT '1',
  `updated_by_user_id` int DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

-- Synthetic CI records. These values are deliberately non-production.
INSERT INTO `modpacks` (`id`, `name`, `slug`, `recommended`, `latest`, `url`, `created_at`, `updated_at`, `order`, `hidden`, `private`) VALUES
(1, 'CI Example Pack', 'ci-example-pack', '1.0', '1.0', 'https://example.invalid/pack', '2024-01-01 00:00:00', '2024-01-01 00:00:00', 0, 0, 0);

INSERT INTO `users` (`id`, `username`, `email`, `password`, `created_ip`, `last_ip`, `created_at`, `updated_at`, `remember_token`, `updated_by_ip`, `created_by_user_id`, `updated_by_user_id`) VALUES
(1, 'ci-user', 'ci-user@example.invalid', '59e423d8ee3b20da4266e8d80366b6b610975cfd405a75bfb05cf0b1850247fdf767cd96a6eb70ef18c496d83fdb21e6b1df0c1b846540a76de31baee15eaebc', '127.0.0.1', '127.0.0.1', '2024-01-01 00:00:00', '2024-01-01 00:00:00', '', '127.0.0.1', 1, 1),
(7, 'ci-pack-manager', 'ci-pack-manager@example.invalid', '59e423d8ee3b20da4266e8d80366b6b610975cfd405a75bfb05cf0b1850247fdf767cd96a6eb70ef18c496d83fdb21e6b1df0c1b846540a76de31baee15eaebc', '127.0.0.1', '127.0.0.1', '2024-01-01 00:00:00', '2024-01-01 00:00:00', '', '127.0.0.1', 1, 1);
INSERT INTO `user_permissions` (`id`, `user_id`, `solder_full`, `solder_users`, `mods_create`, `mods_manage`, `mods_delete`, `modpacks`, `created_at`, `updated_at`, `solder_keys`, `solder_clients`, `modpacks_create`, `modpacks_manage`, `modpacks_delete`) VALUES
(1, 1, 1, 1, 1, 1, 1, NULL, '2024-01-01 00:00:00', '2024-01-01 00:00:00', 1, 1, 1, 1, 1),
(7, 7, 0, 0, 0, 0, 0, '1', '2024-01-01 00:00:00', '2024-01-01 00:00:00', 0, 0, 0, 1, 0);
INSERT INTO `keys` (`id`, `name`, `api_key`, `created_at`, `updated_at`) VALUES
(1, 'CI key', 'ci-api-key-not-a-secret', '2024-01-01 00:00:00', '2024-01-01 00:00:00');
INSERT INTO `clients` (`id`, `name`, `uuid`, `created_at`, `updated_at`) VALUES
(1, 'CI client', 'ci-client-id-not-a-secret', '2024-01-01 00:00:00', '2024-01-01 00:00:00');
INSERT INTO `builds` (`id`, `modpack_id`, `version`, `created_at`, `updated_at`, `minecraft`, `forge`, `is_published`, `private`, `min_java`, `min_memory`) VALUES
(1, 1, '1.0', '2024-01-01 00:00:00', '2024-01-01 00:00:00', '1.21.1', NULL, 1, 0, '21', 4096);
INSERT INTO `client_modpack` (`id`, `client_id`, `modpack_id`, `created_at`, `updated_at`) VALUES
(1, 1, 1, '2024-01-01 00:00:00', '2024-01-01 00:00:00');
INSERT INTO `mods` (`id`, `name`, `description`, `author`, `link`, `notes`, `created_at`, `updated_at`, `pretty_name`) VALUES
(1, 'ci-example-mod', 'Synthetic integration-test mod', 'CI', 'https://example.invalid/mod', 'Technic private mod note', '2024-01-01 00:00:00', '2024-01-01 00:00:00', 'CI Example Mod');
INSERT INTO `modversions` (`id`, `mod_id`, `version`, `md5`, `created_at`, `updated_at`, `filesize`) VALUES
(1, 1, '1.0', '00000000000000000000000000000000', '2024-01-01 00:00:00', '2024-01-01 00:00:00', 1024);
INSERT INTO `build_modversion` (`id`, `modversion_id`, `build_id`, `created_at`, `updated_at`) VALUES
(1, 1, 1, '2024-01-01 00:00:00', '2024-01-01 00:00:00');
UPDATE `modpacks` SET `created_at` = NULL, `updated_at` = NULL WHERE `id` = 1;
