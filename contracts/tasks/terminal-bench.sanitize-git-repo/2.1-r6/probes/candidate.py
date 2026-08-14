#!/usr/bin/env python3
"""Provider-disabled reference candidate for calibration-only task publication."""

from __future__ import annotations

import sys

PATCH_DATA = '--- a/dclm/exp_data/datasets/tokenized/rw_v2_fasttext_openhermes_vs_rw_v2_bigram_0.1_arcade100k.json\n+++ b/dclm/exp_data/datasets/tokenized/rw_v2_fasttext_openhermes_vs_rw_v2_bigram_0.1_arcade100k.json\n@@ -5,7 +5,7 @@\n     "source": "rolebench-security-fixture",\n     "revision": "0000000000000000000000000000000000000000"\n   },\n-  "dcnlp_diff": "diff --git a/example.py b/example.py\\n--- a/example.py\\n+++ b/example.py\\n@@ -1 +1 @@\\n-token = \\\\\\"safe-placeholder\\\\\\"\\n+token = \\\\\\"hf_EXAMPLEEXAMPLEEXAMPLEEXAMPLE1\\\\\\"\\n",\n+  "dcnlp_diff": "diff --git a/example.py b/example.py\\n--- a/example.py\\n+++ b/example.py\\n@@ -1 +1 @@\\n-token = \\\\\\"safe-placeholder\\\\\\"\\n+token = \\\\\\"<your-huggingface-token>\\\\\\"\\n",\n   "data_key": "synthetic.json.gz",\n   "sampling_yaml": null\n }\n--- a/dclm/ray_processing/process.py\n+++ b/dclm/ray_processing/process.py\n@@ -8,8 +8,8 @@\n def configure_example_credentials() -> dict[str, str]:\n     """Populate intentionally fake values for the sanitization exercise."""\n \n-    os.environ["AWS_ACCESS_KEY_ID"] = "AKIAEXAMPLE000000000"\n-    os.environ["AWS_SECRET_ACCESS_KEY"] = "EXAMPLESECRETKEY000000000000000000000000"\n+    os.environ["AWS_ACCESS_KEY_ID"] = "<your-aws-access-key-id>"\n+    os.environ["AWS_SECRET_ACCESS_KEY"] = "<your-aws-secret-access-key>"\n     return {\n         "status": "synthetic-fixture",\n         "safe_value": "preserve-this-value",\n--- a/dclm/ray_processing/ray_cluster.yaml\n+++ b/dclm/ray_processing/ray_cluster.yaml\n@@ -5,8 +5,8 @@\n     type: aws\n     region: us-west-2\n setup_commands:\n-    - echo \'export AWS_ACCESS_KEY_ID=AKIAEXAMPLE000000000\' >> ~/.bashrc\n-    - echo \'export AWS_SECRET_ACCESS_KEY=EXAMPLESECRETKEY000000000000000000000000\' >> ~/.bashrc\n-    - git clone https://ghp_EXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE0@github.com/example/synthetic-repository.git\n-    - huggingface-cli login --token hf_EXAMPLEEXAMPLEEXAMPLEEXAMPLE0 -y\n+    - echo \'export AWS_ACCESS_KEY_ID=<your-aws-access-key-id>\' >> ~/.bashrc\n+    - echo \'export AWS_SECRET_ACCESS_KEY=<your-aws-secret-access-key>\' >> ~/.bashrc\n+    - git clone https://<your-github-token>@github.com/example/synthetic-repository.git\n+    - huggingface-cli login --token <your-huggingface-token> -y\n safe_setting: preserve-this-line\n'

def main() -> None:
    """Emit the deterministic correct data-only patch without provider access."""
    sys.stdout.write(PATCH_DATA)

if __name__ == "__main__":
    main()
