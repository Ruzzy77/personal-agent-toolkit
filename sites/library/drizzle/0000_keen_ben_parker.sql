CREATE TABLE `documents` (
	`id` text PRIMARY KEY NOT NULL,
	`collection` text NOT NULL,
	`date` text NOT NULL,
	`published_at` text NOT NULL,
	`title` text NOT NULL,
	`references_json` text DEFAULT '[]' NOT NULL,
	`canonical_path` text NOT NULL,
	`text_content` text NOT NULL,
	`source_html` text NOT NULL,
	`cover_path` text,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `documents_canonical_path_unique` ON `documents` (`canonical_path`);--> statement-breakpoint
CREATE INDEX `documents_published_at_idx` ON `documents` (`published_at`);--> statement-breakpoint
CREATE INDEX `documents_collection_published_at_idx` ON `documents` (`collection`,`published_at`);