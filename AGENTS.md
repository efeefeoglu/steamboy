# Project overview

This repository automatically identifies and exports promising self-contained highlights from gameplay recordings and game trailers.


# Architecture

- **Deployment:** Hosting is Vercel. 
- **Storage:** Vercel doesn't allow storing  files from the frontend. Use Vercel Blob for storage. If you need it let know the user and tell the environmental variable name for the access token; they will store it on Vercel.
- **DB:** DB is Neon PostgresDB. If you need it, let the user know and share the environmental variable name for the DB URL. And share the SQL query to be able to create the DB on Neon.
