-- Author rotation: a pool of WordPress user IDs to spread posts across
-- instead of every article being attributed to the single Application
-- Password account. Categories don't need a column - the outline step
-- itself now picks a short category name per article (see models.py's
-- ArticleOutline.category), looked up/created in WordPress at publish time.

alter table websites add column if not exists wp_author_ids integer[] not null default '{}';

NOTIFY pgrst, 'reload schema';
