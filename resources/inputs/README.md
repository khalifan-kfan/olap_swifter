# Dataset inputs

One folder per dataset. The folder name is the dataset name.

```
resources/inputs/<dataset name>/
    catalogue/             (required) one or more data dictionaries (.csv / .yaml / .yml)
    data/                  one or more raw CSV exports; only the code tools open them
    eda/                   one EDA per data file; made from data/ if absent
    codes/                 code lists / vocabularies, one file each (e.g. icd10.csv)
    description.yaml       written by `describe(...)`
```

Write the description in one line:

```bash
python -c "from olap_swifter import describe; describe('my_dataset', study='...', main_question='...', one_row_is='...', main_event='...')"
```

Everything here except this README is git-ignored: inputs may contain real patient data.
