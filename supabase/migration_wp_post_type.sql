-- Some sites' themes register their own custom post type/taxonomy for
-- real content instead of using WordPress's standard "post"/"category"
-- (found on dealstackpro.com: a ReHub-theme "blog" post type with its own
-- "blog_category" taxonomy - the admin UI itself relabels "Posts" to
-- "Blog posts" around it). Publishing to the standard /wp/v2/posts
-- endpoint on such a site can succeed (a real database row, 200 OK) while
-- being invisible on the site's actual front end, since the theme's
-- templates only render its own post type.

alter table websites add column if not exists wp_post_type text not null default 'posts';
alter table websites add column if not exists wp_category_taxonomy text not null default 'categories';

NOTIFY pgrst, 'reload schema';
