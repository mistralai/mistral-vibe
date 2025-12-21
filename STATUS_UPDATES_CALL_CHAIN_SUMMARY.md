# Status_Updates Table Call Chain Summary

## Overview
The `Status_Updates` table in the WMS Despatcher application is populated through a well-defined call chain when processing inbound status CSV files (itl*.csv).

## Complete Call Chain

### 1. Main Entry Point
**Function:** `WMS_Despatcher_process()`
- **Location:** `Project_WMS_DESPATCHER.bas`, line 71
- **Purpose:** Main orchestrator that coordinates all WMS despatcher operations
- **Key Action:** Calls `WMSD_CopyInputFiles("CP_INPUT")`

### 2. File Processing
**Function:** `WMSD_CopyInputFiles(cFile_Type As String)`
- **Location:** `Project_WMS_DESPATCHER.bas`, line 263
- **Purpose:** Copies inbound CSV files and processes them
- **Key Actions:**
  - Finds files matching pattern `itl*.csv`
  - For each file, calls `WMDS_Import_Status_File()`

### 3. CSV Parsing
**Function:** `WMDS_Import_Status_File(cFile_Name_Loc As String, cFile_Name As String, cFile_Loc_sucess As String, cFile_Loc_Error As String)`
- **Location:** `Project_WMS_DESPATCHER.bas`, line 117
- **Purpose:** Parses CSV files and extracts status update records
- **Key Actions:**
  - Opens CSV file using `ADO_RSOpenText_vbLf()`
  - For each row:
    - Resets `Status_Fileds` structure
    - Populates structure from CSV columns
    - Calls `WMSD_Insert_Status_Records()` to insert into database

### 4. Database Insertion
**Function:** `WMSD_Insert_Status_Records(cSystem As String, bHeaderInserted As Boolean, cFileName As String)`
- **Location:** `Project_WMS_DESPATCHER.bas`, line 200
- **Purpose:** Inserts status update records into the Status_Updates table
- **Key Actions:**
  - Determines target database (UAT or LIVE)
  - Creates SQL query to select from Status_Updates table
  - Adds new record and populates all fields from `Status_Fileds` structure:
    - TFS_File_Name
    - Record_Type
    - Action
    - Code
    - Dstamp (converted via `WMSD_ConvertToDate()`)
    - Client_Id
    - Reference_Id
    - User_Id
    - Consignment
    - Customer_Id
    - From_Status
    - To_Status
    - Original_Qty
    - Update_Qty
    - Sku_Id
    - Line_Id
    - Condition_Id
    - Owner_Id
    - Qc_Status
    - From_Site_Id
    - To_Site_Id
    - Expiry_Dstamp (converted via `WMSD_ConvertToDate()`)
    - Carrier_Container_Id
    - Parcel_Tracking_Url
    - Container_Id
  - Calls `.Update()` on recordset to persist to database
  - Returns PK_Record_id of inserted record

## Data Flow

1. **CSV Files** (`itl*.csv`) are placed in the CP_INPUT directory
2. `WMS_Despatcher_process()` is called (main entry point)
3. `WMSD_CopyInputFiles("CP_INPUT")` copies files and processes each one
4. `WMDS_Import_Status_File()` parses each CSV file row by row
5. `WMSD_Insert_Status_Records()` inserts each record into the Status_Updates table
6. Files are moved to success or error folders based on processing result

## Target Tables
- `EMP_db.DBO.Status_Updates` (for UAT)
- `EMP_db_Live.DBO.Status_Updates` (for LIVE)

## Helper Functions
- `WMSD_ConvertToDate(dateStr As String)`: Converts 14-character date strings (YYYYMMDDHHMMSS) to VB Date format
- `Status_Structure_Reset()`: Resets the Status_Fileds structure to default values
- `ADO_RSOpenText_vbLf()`: Opens a text file as a recordset with line feed delimiter
- `ADO_GetRSServer()`: Gets a recordset from the server database

## Verification
All functions mentioned in this documentation have been verified to exist in `Project_WMS_DESPATCHER.bas`:
- ✓ `WMS_Despatcher_process()`
- ✓ `WMSD_CopyInputFiles()`
- ✓ `WMDS_Import_Status_File()`
- ✓ `WMSD_Insert_Status_Records()`
