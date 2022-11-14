# [MAS submod converter](https://github.com/Booplicate/MAS-submod-converter) - converter for old-style submods

### Description
This converter helps with updating old submods to the new (header-based) style.

When used, this tool will:
- create a new folder for the new submod
- move all assets to the new folder
- parse your rpy and generate a header for your submod
- convert your rpy into submod modules
- the structure of your submod remains the same

### Usage
Help is available via `python mas_submod_converter.py -h`

Basic example:
- `python mas_submod_converter.py "some/submod/directory" "submod_header.rpy"`

Flags:
- `-d` - dry run - it's recommended to run the command with this flag first, verify the result is correct, then run the command again without the flag
- `-q` - quiet - no stdout output

Parameters:
- `--out-dir` - the output directory, defaults to current working directory

### Requirements
 - `Python 3.10.4`
